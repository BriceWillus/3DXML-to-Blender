# 3DXML to Blender

Blender addon for importing Dassault Systèmes / SolidWorks 3DXML files.

## Features

- Import tessellated 3DXML geometry
- Import assembly hierarchy
- Preserve part transforms
- Automatic Y-Up → Z-Up conversion
- Create a global parent Empty named after the imported file
- Optional vertex merging
- Optional auto smooth by angle (30°)
- Supports Blender 4.x

## Installation

1. Download the latest release ZIP here : https://github.com/BriceWillus/3DXML-to-Blender/releases/tag/v0.7.1
2. Open Blender.
3. Go to Edit → Preferences → Add-ons.
4. Click Install...
5. Select the ZIP file.
6. Enable "Import 3DXML".

## Usage

File → Import → 3DXML (.3dxml)
OR
Drag and drop file to 3DView

### Import Options

| Option | Description |
|----------|----------|
| Scale | Unit conversion factor|
| Merge Vertices | Merge overlapping vertices (you can setup the merge distance)|
| Auto Smooth by Angle | Apply smooth shading with 30° angle (you can setup the angle) |
| Apply Modifier | Apply the Auto Smooth Modifier |
| Align to World | Centers the file's content to the scene with an extra Z offset so the lowest vertice is at Z=0 |
| Merge Objects by Materials | Joins objects that use exactly the same material slots |
| Center Origins to Geometry | Move each imported object's origin to the center of its geometry |

## Supported

- Assembly hierarchy
- Instance transforms
- PolygonalRep geometry
- Face colors
- Drag and drop

## Not Yet Supported

- Textures
- Advanced materials
- NURBS / B-Rep geometry
- Animations

## Tested With

- SolidWorks exports
- Dassault 3DXML assemblies
- Blender 4.x
- Blender 5.x

## License

MIT License
