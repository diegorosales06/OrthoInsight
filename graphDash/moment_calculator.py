def calculate_moment(rx, ry, rz, fx, fy, fz):
    """
    Calculate moment vector using cross product: M = r × F

    Args:
        rx, ry, rz: position vector components (m)
        fx, fy, fz: force vector components (N)

    Returns:
        (mx, my, mz): moment components (N·m)
    """
    mx = ry * fz - rz * fy
    my = rz * fx - rx * fz
    mz = rx * fy - ry * fx
    return mx, my, mz
