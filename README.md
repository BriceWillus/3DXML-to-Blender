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

1. Download the latest release ZIP here : https://github.com/BriceWillus/3DXML-to-Blender/releases/download/v0.4.0/io_scene_3dxml.zip
2. Open Blender.
3. Go to Edit → Preferences → Add-ons.
4. Click Install...
5. Select the ZIP file.
6. Enable "Import 3DXML".

## Usage

File → Import → 3DXML (.3dxml)

### Import Options

| Option | Description |
|----------|----------|
| Scale | Unit conversion factor |
| Merge Vertices | Merge overlapping vertices |
| Auto Smooth by Angle | Apply smooth shading with 30° angle |

## Supported

- Assembly hierarchy
- Instance transforms
- PolygonalRep geometry
- Face colors

## Not Yet Supported

- Textures
- Advanced materials
- NURBS / B-Rep geometry
- Animations

## Tested With

- SolidWorks exports
- Dassault 3DXML assemblies
- Blender 4.x

## License

MIT License
