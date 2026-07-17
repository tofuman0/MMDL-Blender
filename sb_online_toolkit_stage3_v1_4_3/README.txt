SB Online Toolkit — Stage 3 v1.4.3

Fixes:
- Window materials preserve their real MIA alpha values, including semi-transparent glass.
- Projected-light textures use a brightness/black-key mask, removing black rectangular backgrounds even when the MIA alpha channel is fully opaque.
- Headlight and road-reflection textures are made emissive.
- BL floor-light geometry is treated as a shared light effect and remains linked to every selected headlight stage instead of being misread as stage 0 only.
- Existing Stage 3 CARMODS, overfender, number-plate and collision behaviour remains included.

Reimport the MMDL after installing because material nodes and object metadata are built during import.
