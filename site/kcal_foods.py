#!/usr/bin/env python3
"""Собирает docs/kcal/foods.json — блюда и продукты со всего мира для сайта «Калории».

Источник — USDA FoodData Central, FNDDS (survey foods, общественное достояние): ккал, белки, жиры,
углеводы, натрий (соль = натрий × 2,5) на 100 г и типичная порция («Quantity not specified»).
Русские названия — site/kcal_usda_ru.tsv (fdc_id, русское, английское), переведены вручную.
Детское питание и смеси не берём.

usage: python3 site/kcal_foods.py [папка-с-csv]   (без папки — скачает архив USDA во временную)
"""
import csv
import collections
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "docs", "kcal", "foods.json")
RU = os.path.join(HERE, "kcal_usda_ru.tsv")
URL = "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_survey_food_csv_2024-10-31.zip"
NUTR = {"208": "kcal", "203": "p", "204": "f", "205": "c", "307": "na"}  # nutrient_nbr


def source():
    if len(sys.argv) > 1:
        return sys.argv[1]
    tmp = tempfile.mkdtemp()
    with urllib.request.urlopen(URL, timeout=120) as r:
        zipfile.ZipFile(io.BytesIO(r.read())).extractall(tmp)
    return os.path.join(tmp, os.listdir(tmp)[0])


def main():
    d = source()
    rows = lambda name: csv.DictReader(open(os.path.join(d, name), encoding="utf-8"))
    cat = {r["wweia_food_category"]: r["wweia_food_category_description"] for r in rows("wweia_food_category.csv")}
    foods = {r["fdc_id"]: {"cat": cat.get(r["food_category_id"], "")} for r in rows("food.csv")}
    for r in rows("food_nutrient.csv"):
        k = NUTR.get(r["nutrient_id"])
        if k and r["fdc_id"] in foods:
            foods[r["fdc_id"]][k] = float(r["amount"] or 0)
    portions = collections.defaultdict(list)
    for r in rows("food_portion.csv"):
        g = float(r["gram_weight"] or 0)
        if g > 0:
            portions[r["fdc_id"]].append((int(r["seq_num"] or 99), r["portion_description"].lower(), g))
    ru = {}
    with open(RU, encoding="utf-8") as f:
        for line in f:
            fid, name, *_ = line.rstrip("\n").split("\t")
            ru[fid] = name
    out = []
    for fid, f in foods.items():
        c = f["cat"].lower()
        if "kcal" not in f or fid not in ru or c.startswith("baby") or "formula" in c or c == "human milk":
            continue
        ps = sorted(portions.get(fid, []))
        typical = [p for p in ps if p[1].startswith("quantity not specified")] or ps
        out.append([ru[fid], round(f["kcal"]), round(f.get("p", 0), 1), round(f.get("f", 0), 1), round(f.get("c", 0), 1),
                    round(f.get("na", 0) * 2.5 / 1000, 2), round(typical[0][2]) if typical else 0])
    out.sort(key=lambda x: x[0])
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(out)} блюд → {OUT} ({os.path.getsize(OUT) // 1024} КБ)")


if __name__ == "__main__":
    main()
