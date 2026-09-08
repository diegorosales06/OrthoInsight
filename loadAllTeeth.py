import yaml

YAML_FILE = "config\\sensors.yaml"

# Create teeth list: LL8-LL1, LR8-LR1
teeth = [f"LL{i}" for i in range(8, 0, -1)] + \
        [f"LR{i}" for i in range(8, 0, -1)]

# Preserve schema and clear existing sensors
data = {
    "sensors": []
}

# Populate sensors
for tooth in teeth:
    data["sensors"].append({
        "name": tooth.lower(),
        "bus": 0,
        "dev": 0,
        "csb_gpio": 0,
        "tooth": tooth,
        "tooth_type": "premolar"
    })

# Write YAML (overwrites existing contents)
with open(YAML_FILE, "w") as f:
    yaml.safe_dump(
        data,
        f,
        default_flow_style=False,
        sort_keys=False
    )

print(f"Created {len(teeth)} sensor entries in {YAML_FILE}")