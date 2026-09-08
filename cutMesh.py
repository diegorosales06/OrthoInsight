import os
import numpy
import trimesh
import numpy
import scipy


def split_stl(input_stl, output_dir):


    names = ["LL7", "LL6", "LL5", "LL4", "LL3", "LL2", "LL1", "LR1", "LR2", "LR3", "LR4", "LR5", "LR6", "LR7"]
    # Load STL
    mesh = trimesh.load_mesh(input_stl)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Split into connected components
    components = mesh.split(only_watertight=False)

    print(f"Found {len(components)} separate mesh(es).")

    # Export each component
    for i, component in enumerate(components, start=0):
        output_path = os.path.join(output_dir, f"{names[i]}.stl")
        component.export(output_path)

        print(
            f"Saved {output_path} "
            f"(vertices={len(component.vertices)}, "
            f"faces={len(component.faces)})"
        )


if __name__ == "__main__":
    input_stl = r"C:\Users\5035977\Downloads\LOWER_CROWNS_T1.stl"
    output_dir = r"C:\Users\5035977\Downloads\crownMeshes"

    split_stl(input_stl, output_dir)