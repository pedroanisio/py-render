#!/usr/bin/env python3
"""Write assets.manifest.json from scene.xml, recording provenance, licences and SHA-256 of every built file."""
import hashlib, json, os
from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
PROV = {
 "img-print-kyiv": dict(source="https://commons.wikimedia.org/wiki/File:Ночной_Подол_Podil_at_night_(49530393651).jpg", author="spoilt.exile", license="CC BY-SA 2.0", raw="assets/source/kyiv_night.jpg", note="Adapted into a photo print (crop, border, caption, tape); adaptation shared under CC BY-SA 2.0."),
 "img-print-dc": dict(source="https://commons.wikimedia.org/wiki/File:BalticServers_data_center.jpg", author="BalticServers.com", license="CC BY-SA 3.0", raw="assets/source/datacentre.jpg", note="Illustrative; not a Kyiv facility. Adaptation shared under CC BY-SA 3.0."),
 "img-print-starlink": dict(source="https://commons.wikimedia.org/wiki/File:SpaceX_Starlink_User_Terminal_v2_(51740775162).jpg", author="Steve Jurvetson (Flickr: jurvetson)", license="CC BY 2.0", raw="assets/source/starlink.jpg", note="Illustrative user terminal; the station set on fire was a ground station in central Poland."),
 "img-print-blackout": dict(source="https://commons.wikimedia.org/wiki/File:Kyiv_during_the_blackout_caused_by_Russian_missile_attacks,_November_2022.jpg", author="Bektour", license="CC BY 4.0", raw="assets/source/kyiv_blackout.jpg", note="Archive image from November 2022, captioned as such on the print."),
 "img-print-port": dict(source="https://commons.wikimedia.org/wiki/File:Port_of_Odessa_UA_2017_G1.jpg", author="George Chernilevsky", license="Public domain", raw="assets/source/port.jpg", note="Archive image from 2017, captioned as such on the print."),
 "img-map": dict(source="https://github.com/nvkelso/natural-earth-vector (geojson/ne_50m_admin_0_countries.geojson)", author="Natural Earth", license="Public domain", raw="assets/source/ne_50m_admin_0_countries.geojson", note="Crimea drawn inside Ukraine's internationally recognised borders."),
}
ROLE = {
 "img-desk": ("plate", 0.4, "Cutting-mat desk; 240 px / 135 px overscan for rig moves."),
 "img-map": ("plate", 1.0, "Shown at 0.6 scale; Kyiv at map px (2476, 1125); central-Poland marker at (858, 780)."),
 "img-pencil": ("prop", 1.3, "Foreground pencil, lens-blurred in S2."),
 "seq-houses": ("puppet-action", None, "12 frames on twos: all lit (1–3), windows go dark (4–12), no-signal marks from frame 10."),
 "seq-network": ("puppet-action", None, "24 frames on twos: traffic on the upper route (1–8), centre node knocked out (9), rerouted below (10–24)."),
}
root = etree.parse("scene.xml").getroot()
assets = []
for el in root.find("assets"):
    if not isinstance(el.tag, str):
        continue
    tag, i = etree.QName(el).localname, el.get("id")
    if tag == "text":
        continue
    e = {"id": i, "kind": tag, "path": el.get("src")}
    for k in ("width", "height", "first", "last"):
        if el.get(k):
            e[k] = int(el.get(k))
    if el.get("fps"):
        e["fps"] = el.get("fps")
    if tag == "audio":
        e.update(duration=float(el.get("duration")), sampleRate=48000, channels=2)
    if tag == "font":
        e.update(family=el.get("family"), weight=int(el.get("weight")))
    if i in ROLE:
        e["role"], e["depthFactor"], e["capture"] = ROLE[i]
    e["license"] = el.get("license")
    if el.get("credit"):
        e["credit"] = el.get("credit")
    if i in PROV:
        e["provenance"] = dict(PROV[i], rawSha256=sha(PROV[i]["raw"]))
    if tag != "imageSequence":
        e["sha256"] = sha(e["path"])
    e["builtBy"] = "copied font file" if tag == "font" else "tools/build_assets.py"
    e["status"] = "final"
    assets.append(e)
manifest = {
 "manifestVersion": 1, "project": "digital-front", "scene": "scene.xml",
 "frame": {"width": 1920, "height": 1080, "fps": "24", "animationRate": 12},
 "storySources": [
  {"outlet": "The Guardian", "date": "2026-09-26", "title": "Russia strikes at heart of Ukraine’s digital economy with attacks on ISPs and datacentres",
   "url": "https://www.theguardian.com/world/2026/sep/26/russia-strikes-ukraine-digital-economy-attacks-isps-datacentres"},
  {"outlet": "Ukrainska Pravda", "date": "2026-09-25", "title": "Ukraine steps up interceptor production against Russian jet-powered Shaheds",
   "url": "https://www.pravda.com.ua/eng/news/2026/09/25/8055079/"}],
 "assets": assets}
json.dump(manifest, open("assets.manifest.json", "w"), indent=2, ensure_ascii=False)
print(len(assets), "manifest entries")
