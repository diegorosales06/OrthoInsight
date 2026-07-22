#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <gpiod.h>
#include <limits.h>
#include <linux/spi/spidev.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <yaml.h>

#define SPI_SPEED_HZ 2000000U
#define SPI_BITS      8U
#define GPIO_CHIP     "/dev/gpiochip0"

#define CMD_START     0xF0
#define CMD_DATA2     0xE2
#define CMD_BOOT      0xB0
#define CMD_STOP      0xB2
#define CMD_RESET     0xB4
#define CMD_STATUS    0x80
#define CMD_INTERVAL  0x44

#define STT_STANDBY   1
#define STT_READY     3

#define INITIAL_CYCLE_CAPACITY 1024

static const uint8_t CMD_COEFF[6] = {
    0x30, 0x32, 0x34, 0x36, 0x38, 0x3A
};

static volatile sig_atomic_t g_running = 1;

typedef struct {
    char *name;
    unsigned int bus;
    unsigned int dev;
    unsigned int csb_gpio;

    int spi_fd;
    struct gpiod_line *csb_line;
    int32_t coeff[6][6];
} Sensor;

typedef struct {
    Sensor *items;
    size_t count;
    size_t capacity;
} SensorList;

typedef struct {
    double *items;
    size_t count;
    size_t capacity;
} TimingList;

static void handle_sigint(int signo)
{
    (void)signo;
    g_running = 0;
}

static double elapsed_ms(const struct timespec *start,
                         const struct timespec *end)
{
    const double seconds = (double)(end->tv_sec - start->tv_sec);
    const double nanoseconds = (double)(end->tv_nsec - start->tv_nsec);
    return seconds * 1000.0 + nanoseconds / 1000000.0;
}

static void sleep_ms(long milliseconds)
{
    struct timespec req = {
        .tv_sec = milliseconds / 1000,
        .tv_nsec = (milliseconds % 1000) * 1000000L
    };

    while (nanosleep(&req, &req) == -1 && errno == EINTR) {
        if (!g_running) {
            break;
        }
    }
}

static int32_t signed24(const uint8_t bytes[3])
{
    uint32_t value = ((uint32_t)bytes[0] << 16) |
                     ((uint32_t)bytes[1] << 8) |
                     (uint32_t)bytes[2];

    if (value & 0x00800000U) {
        value |= 0xFF000000U;
    }

    return (int32_t)value;
}

static int sensor_list_append(SensorList *list, const Sensor *sensor)
{
    if (list->count == list->capacity) {
        size_t new_capacity = list->capacity == 0 ? 4 : list->capacity * 2;
        Sensor *new_items = realloc(list->items,
                                    new_capacity * sizeof(*new_items));
        if (new_items == NULL) {
            return -1;
        }
        list->items = new_items;
        list->capacity = new_capacity;
    }

    list->items[list->count++] = *sensor;
    return 0;
}

static int timing_list_append(TimingList *list, double value)
{
    if (list->count == list->capacity) {
        size_t new_capacity = list->capacity == 0
                                  ? INITIAL_CYCLE_CAPACITY
                                  : list->capacity * 2;
        double *new_items = realloc(list->items,
                                    new_capacity * sizeof(*new_items));
        if (new_items == NULL) {
            return -1;
        }
        list->items = new_items;
        list->capacity = new_capacity;
    }

    list->items[list->count++] = value;
    return 0;
}

static char *duplicate_yaml_scalar(const yaml_event_t *event)
{
    size_t length = event->data.scalar.length;
    char *copy = malloc(length + 1);
    if (copy == NULL) {
        return NULL;
    }

    memcpy(copy, event->data.scalar.value, length);
    copy[length] = '\0';
    return copy;
}

static int parse_unsigned(const char *text,
                          const char *field_name,
                          unsigned int *value)
{
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 10);

    if (errno != 0 || end == text || *end != '\0' ||
        parsed > UINT_MAX) {
        fprintf(stderr, "Invalid %s value: %s\n", field_name, text);
        return -1;
    }

    *value = (unsigned int)parsed;
    return 0;
}

/*
 * Reads this structure:
 *
 * sensors:
 *   - name: "Cell 1"
 *     bus: 0
 *     dev: 0
 *     csb_gpio: 13
 */
static int load_sensor_config(const char *path, SensorList *sensors)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        fprintf(stderr, "Unable to open config '%s': %s\n",
                path, strerror(errno));
        return -1;
    }

    yaml_parser_t parser;
    if (!yaml_parser_initialize(&parser)) {
        fprintf(stderr, "Failed to initialize libyaml parser\n");
        fclose(file);
        return -1;
    }
    yaml_parser_set_input_file(&parser, file);

    yaml_event_t event;
    int in_sensors_sequence = 0;
    int in_sensor_mapping = 0;
    char *pending_key = NULL;

    Sensor current = {
        .name = NULL,
        .bus = 0,
        .dev = 0,
        .csb_gpio = 0,
        .spi_fd = -1,
        .csb_line = NULL
    };

    int have_name = 0;
    int have_bus = 0;
    int have_dev = 0;
    int have_csb_gpio = 0;
    int result = -1;

    for (;;) {
        if (!yaml_parser_parse(&parser, &event)) {
            fprintf(stderr, "YAML parse error in '%s': %s\n",
                    path,
                    parser.problem != NULL ? parser.problem
                                           : "unknown parser error");
            goto cleanup;
        }

        yaml_event_type_t type = event.type;

        if (type == YAML_STREAM_END_EVENT) {
            yaml_event_delete(&event);
            break;
        }

        if (type == YAML_SCALAR_EVENT) {
            char *value = duplicate_yaml_scalar(&event);
            if (value == NULL) {
                yaml_event_delete(&event);
                fprintf(stderr, "Out of memory while parsing YAML\n");
                goto cleanup;
            }

            if (!in_sensors_sequence) {
                if (strcmp(value, "sensors") == 0) {
                    free(pending_key);
                    pending_key = value;
                    value = NULL;
                }
            } else if (in_sensor_mapping) {
                if (pending_key == NULL) {
                    pending_key = value;
                    value = NULL;
                } else {
                    if (strcmp(pending_key, "name") == 0) {
                        free(current.name);
                        current.name = value;
                        value = NULL;
                        have_name = 1;
                    } else if (strcmp(pending_key, "bus") == 0) {
                        if (parse_unsigned(value, "bus",
                                           &current.bus) != 0) {
                            free(value);
                            yaml_event_delete(&event);
                            goto cleanup;
                        }
                        have_bus = 1;
                    } else if (strcmp(pending_key, "dev") == 0) {
                        if (parse_unsigned(value, "dev",
                                           &current.dev) != 0) {
                            free(value);
                            yaml_event_delete(&event);
                            goto cleanup;
                        }
                        have_dev = 1;
                    } else if (strcmp(pending_key, "csb_gpio") == 0) {
                        if (parse_unsigned(value, "csb_gpio",
                                           &current.csb_gpio) != 0) {
                            free(value);
                            yaml_event_delete(&event);
                            goto cleanup;
                        }
                        have_csb_gpio = 1;
                    }

                    free(pending_key);
                    pending_key = NULL;
                }
            }

            free(value);
        } else if (type == YAML_SEQUENCE_START_EVENT) {
            if (pending_key != NULL &&
                strcmp(pending_key, "sensors") == 0) {
                in_sensors_sequence = 1;
                free(pending_key);
                pending_key = NULL;
            }
        } else if (type == YAML_MAPPING_START_EVENT &&
                   in_sensors_sequence && !in_sensor_mapping) {
            in_sensor_mapping = 1;
            memset(&current, 0, sizeof(current));
            current.spi_fd = -1;
            current.csb_line = NULL;
            have_name = have_bus = have_dev = have_csb_gpio = 0;
        } else if (type == YAML_MAPPING_END_EVENT &&
                   in_sensor_mapping) {
            if (!have_name || !have_bus || !have_dev || !have_csb_gpio) {
                fprintf(stderr,
                        "Each sensor requires name, bus, dev, and csb_gpio\n");
                yaml_event_delete(&event);
                goto cleanup;
            }

            if (sensor_list_append(sensors, &current) != 0) {
                fprintf(stderr, "Out of memory while storing sensor config\n");
                yaml_event_delete(&event);
                goto cleanup;
            }

            current.name = NULL; /* ownership moved into sensors */
            in_sensor_mapping = 0;
        } else if (type == YAML_SEQUENCE_END_EVENT &&
                   in_sensors_sequence) {
            in_sensors_sequence = 0;
        }

        yaml_event_delete(&event);
    }

    if (sensors->count == 0) {
        fprintf(stderr, "No sensors found in '%s'\n", path);
        goto cleanup;
    }

    result = 0;

cleanup:
    free(pending_key);
    free(current.name);
    yaml_parser_delete(&parser);
    fclose(file);
    return result;
}

static int spi_write_then_read(Sensor *sensor,
                               const uint8_t *tx,
                               size_t tx_length,
                               uint8_t *rx,
                               size_t rx_length)
{
    if (gpiod_line_set_value(sensor->csb_line, 0) < 0) {
        fprintf(stderr, "%s: failed to drive CSB low: %s\n",
                sensor->name, strerror(errno));
        return -1;
    }

    int result = 0;

    if (tx_length > 0) {
        struct spi_ioc_transfer transfer = {
            .tx_buf = (uintptr_t)tx,
            .rx_buf = 0,
            .len = (uint32_t)tx_length,
            .speed_hz = SPI_SPEED_HZ,
            .delay_usecs = 0,
            .bits_per_word = SPI_BITS,
            .cs_change = 0
        };

        if (ioctl(sensor->spi_fd, SPI_IOC_MESSAGE(1), &transfer) < 0) {
            fprintf(stderr, "%s: SPI transmit failed: %s\n",
                    sensor->name, strerror(errno));
            result = -1;
            goto finish;
        }
    }

    if (rx_length > 0) {
        uint8_t *zeros = calloc(rx_length, 1);
        if (zeros == NULL) {
            fprintf(stderr, "Out of memory during SPI receive\n");
            result = -1;
            goto finish;
        }

        struct spi_ioc_transfer transfer = {
            .tx_buf = (uintptr_t)zeros,
            .rx_buf = (uintptr_t)rx,
            .len = (uint32_t)rx_length,
            .speed_hz = SPI_SPEED_HZ,
            .delay_usecs = 0,
            .bits_per_word = SPI_BITS,
            .cs_change = 0
        };

        if (ioctl(sensor->spi_fd, SPI_IOC_MESSAGE(1), &transfer) < 0) {
            fprintf(stderr, "%s: SPI receive failed: %s\n",
                    sensor->name, strerror(errno));
            result = -1;
        }

        free(zeros);
    }

finish:
    if (gpiod_line_set_value(sensor->csb_line, 1) < 0) {
        fprintf(stderr, "%s: failed to drive CSB high: %s\n",
                sensor->name, strerror(errno));
        result = -1;
    }

    return result;
}

static int sensor_command(Sensor *sensor,
                          const uint8_t *tx,
                          size_t tx_length,
                          uint8_t *rx,
                          size_t rx_length)
{
    if (spi_write_then_read(sensor, tx, tx_length, rx, rx_length) != 0) {
        return -1;
    }

    if (rx_length == 0) {
        return 0;
    }

    if (rx[0] != 0x00) {
        fprintf(stderr,
                "%s: command 0x%02X returned status 0x%02X\n",
                sensor->name, tx[0], rx[0]);
        return -1;
    }

    return 0;
}

static int sensor_wait_for_state(Sensor *sensor, uint8_t state)
{
    const uint8_t command = CMD_STATUS;
    uint8_t response[4];

    for (int attempt = 0; attempt < 10; ++attempt) {
        sleep_ms(20);

        if (sensor_command(sensor, &command, 1,
                           response, sizeof(response)) != 0) {
            return -1;
        }

        if (response[3] == state) {
            return 0;
        }
    }

    fprintf(stderr, "%s: timeout waiting for state %u\n",
            sensor->name, state);
    return -1;
}

static int open_sensor_spi(Sensor *sensor)
{
    char device_path[64];
    int length = snprintf(device_path, sizeof(device_path),
                          "/dev/spidev%u.%u", sensor->bus, sensor->dev);
    if (length < 0 || (size_t)length >= sizeof(device_path)) {
        fprintf(stderr, "%s: SPI device path is too long\n", sensor->name);
        return -1;
    }

    sensor->spi_fd = open(device_path, O_RDWR);
    if (sensor->spi_fd < 0) {
        fprintf(stderr, "%s: unable to open %s: %s\n",
                sensor->name, device_path, strerror(errno));
        return -1;
    }

    uint8_t mode = SPI_MODE_3;
    uint8_t bits = SPI_BITS;
    uint32_t speed = SPI_SPEED_HZ;

    if (ioctl(sensor->spi_fd, SPI_IOC_WR_MODE, &mode) < 0 ||
        ioctl(sensor->spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(sensor->spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) {
        fprintf(stderr, "%s: unable to configure %s: %s\n",
                sensor->name, device_path, strerror(errno));
        close(sensor->spi_fd);
        sensor->spi_fd = -1;
        return -1;
    }

    /* Match Python's one-byte dummy clock after opening the SPI device. */
    uint8_t dummy = 0x00;
    struct spi_ioc_transfer transfer = {
        .tx_buf = (uintptr_t)&dummy,
        .rx_buf = 0,
        .len = 1,
        .speed_hz = SPI_SPEED_HZ,
        .bits_per_word = SPI_BITS
    };

    if (ioctl(sensor->spi_fd, SPI_IOC_MESSAGE(1), &transfer) < 0) {
        fprintf(stderr, "%s: dummy SPI clock failed: %s\n",
                sensor->name, strerror(errno));
        close(sensor->spi_fd);
        sensor->spi_fd = -1;
        return -1;
    }

    return 0;
}

static int initialize_sensor(Sensor *sensor)
{
    uint8_t response[21];

    const uint8_t reset = CMD_RESET;
    if (sensor_command(sensor, &reset, 1, response, 1) != 0 ||
        sensor_wait_for_state(sensor, STT_STANDBY) != 0) {
        return -1;
    }

    const uint8_t boot = CMD_BOOT;
    if (sensor_command(sensor, &boot, 1, response, 1) != 0 ||
        sensor_wait_for_state(sensor, STT_READY) != 0) {
        return -1;
    }

    for (size_t axis = 0; axis < 6; ++axis) {
        if (sensor_command(sensor, &CMD_COEFF[axis], 1,
                           response, 19) != 0) {
            return -1;
        }

        for (size_t coefficient = 0; coefficient < 6; ++coefficient) {
            size_t offset = 1 + coefficient * 3;
            sensor->coeff[axis][coefficient] =
                signed24(&response[offset]);
        }
    }

    const uint8_t interval_command[4] = {
        CMD_INTERVAL, 0x00, 0x00, 0x00
    };
    if (sensor_command(sensor, interval_command,
                       sizeof(interval_command), response, 1) != 0) {
        return -1;
    }

    const uint8_t start = CMD_START;
    if (sensor_command(sensor, &start, 1, response, 1) != 0) {
        return -1;
    }

    sleep_ms(10);
    return 0;
}

static int read_forces(Sensor *sensor, double forces[3])
{
    const uint8_t command = CMD_DATA2;
    uint8_t response[21];

    if (sensor_command(sensor, &command, 1,
                       response, sizeof(response)) != 0) {
        return -1;
    }

    int32_t adc[6];
    for (size_t k = 0; k < 6; ++k) {
        adc[k] = signed24(&response[3 + k * 3]);
    }

    for (size_t axis = 0; axis < 3; ++axis) {
        int64_t accumulator = 0;
        for (size_t k = 0; k < 6; ++k) {
            accumulator +=
                (int64_t)sensor->coeff[axis][k] * (int64_t)adc[k];
        }

        /*
         * Match Python exactly:
         * int(acc / 2048) / 1000.0
         *
         * C integer division truncates toward zero, as Python int() does.
         */
        int64_t scaled = accumulator / 2048;
        forces[axis] = (double)scaled / 1000.0;
    }

    return 0;
}

static void stop_sensor(Sensor *sensor)
{
    if (sensor->spi_fd >= 0 && sensor->csb_line != NULL) {
        const uint8_t stop = CMD_STOP;
        uint8_t response[1];
        (void)sensor_command(sensor, &stop, 1, response, 1);
    }

    if (sensor->spi_fd >= 0) {
        close(sensor->spi_fd);
        sensor->spi_fd = -1;
    }

    if (sensor->csb_line != NULL) {
        gpiod_line_release(sensor->csb_line);
        sensor->csb_line = NULL;
    }
}

static int build_default_config_path(char path[PATH_MAX])
{
    char executable_path[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe",
                              executable_path,
                              sizeof(executable_path) - 1);
    if (length < 0) {
        fprintf(stderr, "Unable to resolve executable path: %s\n",
                strerror(errno));
        return -1;
    }
    executable_path[length] = '\0';

    char *last_slash = strrchr(executable_path, '/');
    if (last_slash == NULL) {
        fprintf(stderr, "Unexpected executable path: %s\n",
                executable_path);
        return -1;
    }
    *last_slash = '\0';

    int written = snprintf(path, PATH_MAX, "%s/config/sensors.yaml",
                           executable_path);
    if (written < 0 || written >= PATH_MAX) {
        fprintf(stderr, "Config path is too long\n");
        return -1;
    }

    return 0;
}

static void free_sensor_list(SensorList *sensors)
{
    if (sensors == NULL) {
        return;
    }

    for (size_t i = 0; i < sensors->count; ++i) {
        free(sensors->items[i].name);
    }

    free(sensors->items);
    sensors->items = NULL;
    sensors->count = 0;
    sensors->capacity = 0;
}

int main(int argc, char **argv)
{
    int exit_code = EXIT_FAILURE;
    SensorList sensors = {0};
    TimingList cycle_times = {0};
    struct gpiod_chip *gpio_chip = NULL;
    double (*forces)[3] = NULL;

    char default_config_path[PATH_MAX];
    const char *config_path = NULL;

    if (argc > 2) {
        fprintf(stderr, "Usage: %s [config/sensors.yaml]\n", argv[0]);
        return EXIT_FAILURE;
    }

    if (argc == 2) {
        config_path = argv[1];
    } else {
        if (build_default_config_path(default_config_path) != 0) {
            return EXIT_FAILURE;
        }
        config_path = default_config_path;
    }

    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = handle_sigint;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) < 0) {
        fprintf(stderr, "Unable to install SIGINT handler: %s\n",
                strerror(errno));
        return EXIT_FAILURE;
    }

    if (load_sensor_config(config_path, &sensors) != 0) {
        goto cleanup;
    }

    gpio_chip = gpiod_chip_open(GPIO_CHIP);
    if (gpio_chip == NULL) {
        fprintf(stderr, "Unable to open %s: %s\n",
                GPIO_CHIP, strerror(errno));
        goto cleanup;
    }

    /*
     * Phase 1: claim every configured CSB line and park it HIGH before
     * opening any SPI device, matching the Python setup order.
     */
    for (size_t i = 0; i < sensors.count; ++i) {
        Sensor *sensor = &sensors.items[i];

        sensor->csb_line =
            gpiod_chip_get_line(gpio_chip, sensor->csb_gpio);
        if (sensor->csb_line == NULL) {
            fprintf(stderr, "%s: unable to get GPIO %u: %s\n",
                    sensor->name, sensor->csb_gpio, strerror(errno));
            goto cleanup;
        }

        if (gpiod_line_request_output(sensor->csb_line,
                                      "mms101-stream",
                                      1) < 0) {
            fprintf(stderr,
                    "%s: unable to claim GPIO %u as output-high: %s\n",
                    sensor->name, sensor->csb_gpio, strerror(errno));
            goto cleanup;
        }
    }

    /* Phase 2: open/configure each SPI device. */
    for (size_t i = 0; i < sensors.count; ++i) {
        if (open_sensor_spi(&sensors.items[i]) != 0) {
            goto cleanup;
        }
    }

    for (size_t i = 0; i < sensors.count; ++i) {
        printf("Initializing %s...\n", sensors.items[i].name);
        fflush(stdout);

        if (initialize_sensor(&sensors.items[i]) != 0) {
            goto cleanup;
        }
    }

    printf("\nStreaming %zu sensor(s)  (Ctrl-C to stop)\n\n",
           sensors.count);

    forces = calloc(sensors.count, sizeof(*forces));
    if (forces == NULL) {
        fprintf(stderr, "Out of memory for force readings\n");
        goto cleanup;
    }

    while (g_running) {
        struct timespec start;
        struct timespec end;

        if (clock_gettime(CLOCK_MONOTONIC_RAW, &start) != 0) {
            fprintf(stderr, "clock_gettime failed: %s\n",
                    strerror(errno));
            goto cleanup;
        }

        /*
         * Deliberately sequential and in YAML order, matching:
         * forces = [s.read_forces() for s in sensors]
         */
        for (size_t i = 0; i < sensors.count; ++i) {
            if (read_forces(&sensors.items[i], forces[i]) != 0) {
                goto cleanup;
            }
        }

        if (clock_gettime(CLOCK_MONOTONIC_RAW, &end) != 0) {
            fprintf(stderr, "clock_gettime failed: %s\n",
                    strerror(errno));
            goto cleanup;
        }

        double cycle_ms = elapsed_ms(&start, &end);
        if (timing_list_append(&cycle_times, cycle_ms) != 0) {
            fprintf(stderr, "Out of memory while recording cycle times\n");
            goto cleanup;
        }

        for (size_t i = 0; i < sensors.count; ++i) {
            printf("%s: Fx=%+7.3f Fy=%+7.3f Fz=%+7.3f N",
                   sensors.items[i].name,
                   forces[i][0], forces[i][1], forces[i][2]);

            if (i + 1 < sensors.count) {
                printf("   |   ");
            }
        }
        printf("   [%.2f ms]\n", cycle_ms);
        fflush(stdout);

        sleep_ms(100);
    }

    exit_code = EXIT_SUCCESS;

cleanup:
    printf("\nStopping...\n");

    for (size_t i = 0; i < sensors.count; ++i) {
        stop_sensor(&sensors.items[i]);
    }

    if (gpio_chip != NULL) {
        gpiod_chip_close(gpio_chip);
    }

    if (cycle_times.count > 0) {
        double sum = 0.0;
        double minimum = cycle_times.items[0];
        double maximum = cycle_times.items[0];

        for (size_t i = 0; i < cycle_times.count; ++i) {
            double value = cycle_times.items[i];
            sum += value;
            if (value < minimum) {
                minimum = value;
            }
            if (value > maximum) {
                maximum = value;
            }
        }

        printf("\n-- Timing Summary --\n");
        printf("Cycles:  %zu\n", cycle_times.count);
        printf("Average: %.2f ms\n", sum / (double)cycle_times.count);
        printf("Min:     %.2f ms\n", minimum);
        printf("Max:     %.2f ms\n", maximum);
    }

    free(forces);
    free(cycle_times.items);
    free_sensor_list(&sensors);

    return exit_code;
}
