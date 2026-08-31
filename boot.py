import storage

# Give CircuitPython write access so web-uploaded bitmaps can persist.
# While running normally, the host sees CIRCUITPY as read-only. Hold BOOTSEL
# and enter the UF2 bootloader when firmware files need to be replaced.
storage.remount("/", readonly=False)
