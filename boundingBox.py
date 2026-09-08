import trimesh
mesh = trimesh.load("assets_src\\LL5.stl")

# Axis-aligned bounding box (AABB)
bbox = mesh.bounding_box
bbox.visual.face_colors = [255, 0, 0, 80] # red, alpha=80/255


# Display mesh and bounding box together
scene = trimesh.Scene([mesh, bbox])

scene.show()