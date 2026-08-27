# Changes Today

Date: 2026-08-24

## Pi View System
- Extracted the Pi direction math into `pi_view_simulation.py`.
- Added shared helpers for default Pi setup, input normalization, and fixed-length view-vector calculation.
- Switched the direction settings to `yaw`, `pitch`, and `roll`.
- Kept backward compatibility for older field names like `view_horizontal_deg` and `view_vertical_deg`.
- Defined yaw as rotation around the Z axis and pitch as up/down tilt.
- Kept roll as an input field, even though a line-based view vector does not visually show roll.

## Web Interface
- Updated `webinterface.py` to import the Pi calculation helpers from `pi_view_simulation.py`.
- Added an editable Pi setup table with position and direction fields.
- Added a toggle to switch the 3D aspect mode between `cube` and `data`.
- Set the 3D scene to use orthographic projection.
- Rendered Pi view direction as a thin line with fixed length.
- Made the Pi markers smaller for a cleaner 3D view.
- Preserved the Pi data while loading and displaying the trajectory plot.

## Table Styling
- Darkened hover, active, and selected cell backgrounds in `assets/style.css`.
- Improved readability so table values stay visible when hovering or selecting cells.

## Plot Behavior
- Enforced equal scaling behavior in the 3D plot with configurable aspect mode.
- Adjusted the view-vector math so the displayed line length stays constant regardless of direction.

## Notes
- The trajectory pipeline still loads the generated bird data and keeps the Pi metadata in the output structure.
- The new UI controls are backward compatible with the older Pi field names.

Date: 2026-08-27

## Camera Geometry
- Fixed the Pi view-vector length at `400`.
- Added a square plane at the end of each Pi view vector.
- Added shared basis and square-corner helpers so the plane orientation matches the vector direction.

## View Volume Filtering
- Added helpers in `pi_view_simulation.py` to test whether 3D points lie inside the pyramid volume between a Pi and its end plane.
- Added a function to collect all matching points for each Pi.
- Added a new button in `webinterface.py` to filter all trajectory points to those inside at least one Pi pyramid.
- Made the filter overwrite `vogel_flugbahnen_determined.json` with only the kept points.
- Added a status message that reports how many points were kept and how many were removed.
