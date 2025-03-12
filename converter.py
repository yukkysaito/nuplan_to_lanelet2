import geopandas as gpd
from shapely.geometry import Polygon, LineString
from pyproj import Transformer
from lxml import etree
import pandas as pd
import numpy as np
import fiona
import os

# 入力GeoPackageファイル
gpkg_file = r"/media/yukky/Extreme SSD/nuplan/uncompressed_files/maps/us-ma-boston/9.12.1817/map.gpkg"  # rawプレフィックスでパスを指定
print(f"ファイルは存在しますか？: {os.path.exists(gpkg_file)}")

# 利用可能なレイヤーを表示
print("利用可能なレイヤー:")
for layer in fiona.listlayers(gpkg_file):
    print(f"- {layer}")

# GeoPackageから必要レイヤーを読み込む - レイヤー名を実際のものに修正
lanes_gdf = gpd.read_file(gpkg_file, layer="lanes_polygons")  # 'lanes'から'lanes_polygons'に修正
connectors_gdf = gpd.read_file(gpkg_file, layer="lane_connectors")
try:
    stop_gdf = gpd.read_file(gpkg_file, layer="stop_polygons")  # 'stop_lines'から'stop_polygons'に修正
except Exception as e:
    print(f"停止線データの読み込みエラー: {e}")
    stop_gdf = None
try:
    tl_gdf = gpd.read_file(gpkg_file, layer="traffic_lights")
except Exception as e:
    print(f"信号機データの読み込みエラー: {e}")
    tl_gdf = None

# 座標系の確認と変換器の準備
input_crs = lanes_gdf.crs
output_crs = "EPSG:4326"
print(f"入力座標系: {input_crs}")
if input_crs and input_crs != output_crs:
    transformer = Transformer.from_crs(input_crs, output_crs, always_xy=True)
    print(f"座標変換を行います: {input_crs} → {output_crs}")
else:
    transformer = None
    print("座標変換は不要です")

# レーンおよびレーンコネクタ全てを一つに統合
print(f"レーン数: {len(lanes_gdf)}, コネクタ数: {len(connectors_gdf)}")
all_segments = pd.concat([lanes_gdf, connectors_gdf], ignore_index=True)
print(f"処理対象セグメント数: {len(all_segments)}")

# 抽出した左右境界ラインを保持するリスト
left_boundaries = []
right_boundaries = []

# レーン/レーンコネクタ毎に境界線抽出
for idx, row in all_segments.iterrows():
    geom = row.geometry
    if geom is None:
        continue
    # PolygonまたはMultiPolygonに対処
    if geom.geom_type == "Polygon":
        exterior = geom.exterior
    elif geom.geom_type == "MultiPolygon":
        # 複数ポリゴンは一つ目を代表として使用（必要に応じ複数処理）
        if len(geom.geoms) == 0:
            continue
        exterior = geom.geoms[0].exterior
    else:
        continue  # レーンは通常Polygon想定

    coords = list(exterior.coords)[:-1]  # 外周座標列（最後の重複点除外）
    n = len(coords)
    if n < 4:  # 少なくとも4点必要（左右の境界に各2点ずつ）
        continue

    # 各エッジの長さを計算
    lengths = []
    for i in range(n):
        j = (i + 1) % n
        dx = coords[j][0] - coords[i][0]
        dy = coords[j][1] - coords[i][1]
        lengths.append((dx**2 + dy**2) ** 0.5)
    
    # 長さが短いエッジを二つ特定
    short_edges_indices = np.argsort(lengths)[:min(2, n-2)]  # 最低でもn-2点残す
    if len(short_edges_indices) < 2:
        # 短いエッジが1つしか見つからない場合は対向する位置のエッジも選択
        if len(short_edges_indices) == 1:
            e1 = short_edges_indices[0]
            e2 = (e1 + n//2) % n  # 対向する位置のエッジを選択
        else:
            continue  # エッジが見つからない場合はスキップ
    else:
        e1, e2 = sorted(short_edges_indices.tolist())
    
    # 短辺を除いた左側・右側の座標列を取得
    start1 = (e1 + 1) % n
    end1 = e2
    start2 = (e2 + 1) % n
    end2 = e1
    
    coords1 = coords[start1:end1+1] if start1 <= end1 else coords[start1:] + coords[:end1+1]
    coords2 = coords[start2:end2+1] if start2 <= end2 else coords[start2:] + coords[:end2+1]
    
    # 座標点が2点以上あることを確認してからLineStringを作成
    if len(coords1) >= 2:
        left_boundaries.append(LineString(coords1))
    else:
        print(f"警告: インデックス {idx} のセグメントから左側境界線を作成できませんでした（座標点数: {len(coords1)}）")
        
    if len(coords2) >= 2:
        right_boundaries.append(LineString(coords2))
    else:
        print(f"警告: インデックス {idx} のセグメントから右側境界線を作成できませんでした（座標点数: {len(coords2)}）")

# 左右境界線の数が一致しているか確認
if len(left_boundaries) != len(right_boundaries):
    print(f"警告: 左境界線数 ({len(left_boundaries)}) と右境界線数 ({len(right_boundaries)}) が一致しません")
    # 少ない方に合わせる
    min_count = min(len(left_boundaries), len(right_boundaries))
    left_boundaries = left_boundaries[:min_count]
    right_boundaries = right_boundaries[:min_count]

print(f"有効な境界線ペア数: {len(left_boundaries)}")

# OSM XMLのルート要素を作成
osm_root = etree.Element("osm", version="0.6")

# ノード管理
node_id_map = {}
next_node_id = 1

def create_node(lat, lon, ele=None):
    """lat, lon座標のノードをosm_rootに追加（既存なら再利用）し、ノードIDを返す"""
    global next_node_id
    key = (round(lat, 6), round(lon, 6), round(ele, 3) if ele is not None else None)
    if key in node_id_map:
        return node_id_map[key]
    node_id = next_node_id
    next_node_id += 1
    node_id_map[key] = node_id
    node_attrs = {"id": str(node_id), "lat": str(lat), "lon": str(lon)}
    if ele is not None:
        node_attrs["ele"] = str(ele)
    etree.SubElement(osm_root, "node", **node_attrs)
    return node_id

# ウェイおよびリレーションID管理
next_way_id = 1
next_rel_id = 1

# 境界線ウェイとLaneletリレーション生成
for left_line, right_line in zip(left_boundaries, right_boundaries):
    # 左境界ウェイ
    way_elem_left = etree.SubElement(osm_root, "way", id=str(next_way_id))
    left_way_id = next_way_id
    next_way_id += 1
    for (x, y) in left_line.coords:
        if transformer:
            lon, lat = transformer.transform(x, y)
        else:
            lat, lon = y, x
        nid = create_node(lat, lon)
        etree.SubElement(way_elem_left, "nd", ref=str(nid))
    etree.SubElement(way_elem_left, "tag", k="subtype", v="solid")
    etree.SubElement(way_elem_left, "tag", k="type", v="LineString")

    # 右境界ウェイ
    way_elem_right = etree.SubElement(osm_root, "way", id=str(next_way_id))
    right_way_id = next_way_id
    next_way_id += 1
    for (x, y) in right_line.coords:
        if transformer:
            lon, lat = transformer.transform(x, y)
        else:
            lat, lon = y, x
        nid = create_node(lat, lon)
        etree.SubElement(way_elem_right, "nd", ref=str(nid))
    etree.SubElement(way_elem_right, "tag", k="subtype", v="solid")
    etree.SubElement(way_elem_right, "tag", k="type", v="LineString")

    # Laneletリレーション
    rel_elem = etree.SubElement(osm_root, "relation", id=str(next_rel_id))
    next_rel_id += 1
    etree.SubElement(rel_elem, "member", type="way", ref=str(left_way_id), role="left")
    etree.SubElement(rel_elem, "member", type="way", ref=str(right_way_id), role="right")
    etree.SubElement(rel_elem, "tag", k="type", v="lanelet")
    etree.SubElement(rel_elem, "tag", k="subtype", v="road")
    etree.SubElement(rel_elem, "tag", k="location", v="urban")
    etree.SubElement(rel_elem, "tag", k="one_way", v="yes")

# 停止線ウェイの追加
if stop_gdf is not None:
    for idx, row in stop_gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        # ポリゴンの場合は代表する一辺を取得（簡易実装）
        if geom.geom_type == "Polygon":
            centroid = geom.centroid
            exterior = geom.exterior
            coords = list(exterior.coords)[:-1]
            # 重心に最も近いエッジを探す
            min_idx = None; min_dist = float("inf")
            for i in range(len(coords)):
                j = (i+1) % len(coords)
                line = LineString([coords[i], coords[j]])
                dist = line.distance(centroid)
                if dist < min_dist:
                    min_dist = dist; min_idx = i
            if min_idx is None:
                continue
            j = (min_idx+1) % len(coords)
            stop_line = LineString([coords[min_idx], coords[j]])
        elif geom.geom_type == "LineString":
            stop_line = geom
        else:
            continue
        # ウェイ生成
        way_elem = etree.SubElement(osm_root, "way", id=str(next_way_id))
        next_way_id += 1
        for (x, y) in stop_line.coords:
            if transformer:
                lon, lat = transformer.transform(x, y)
            else:
                lat, lon = y, x
            nid = create_node(lat, lon)
            etree.SubElement(way_elem, "nd", ref=str(nid))
        etree.SubElement(way_elem, "tag", k="subtype", v="stop_line")
        etree.SubElement(way_elem, "tag", k="type", v="LineString")
        # （必要ならLaneletへの関係付けをここで処理）

# 信号機ノードのタグ付与
if tl_gdf is not None:
    for idx, row in tl_gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        points = []
        if geom.geom_type == "Point":
            points = [geom]
        elif geom.geom_type == "MultiPoint":
            points = list(geom.geoms)
        else:
            continue
        for pt in points:
            x, y = pt.x, pt.y
            if transformer:
                lon, lat = transformer.transform(x, y)
            else:
                lat, lon = y, x
            nid = create_node(lat, lon)
            # 既存のnode要素にtraffic_lightタグを追加
            node_elem = osm_root.find(f"node[@id='{nid}']")
            if node_elem is not None:
                etree.SubElement(node_elem, "tag", k="subtype", v="traffic_light")

# XMLツリーをファイル保存
tree = etree.ElementTree(osm_root)
tree.write("nuplan_lanelet_map.osm", encoding="UTF-8", xml_declaration=True, pretty_print=True)
print("Converted Lanelet2 map saved to nuplan_lanelet_map.osm")
