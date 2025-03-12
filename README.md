# nuplan_to_lanelet2

## Requirements

```
conda create -n nuplan_env python=3.10
conda activate nuplan_env

conda config --add channels conda-forge
conda config --set channel_priority strict

conda install geopandas pyproj lxml shapely numpy numexpr bottleneck

python3 converter.py

```
