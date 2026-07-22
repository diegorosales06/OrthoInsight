CC := gcc
CFLAGS := -O2 -Wall -Wextra -Wpedantic -std=c11
LDLIBS := -lgpiod -lyaml

TARGET := stream_c
SRC := stream.c

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f $(TARGET)
