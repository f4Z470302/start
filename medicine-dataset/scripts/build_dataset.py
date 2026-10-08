import json, os, re, time, hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET
import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE=os.getenv("SOURCE_BASE","https://www.1mg.com")
SOURCE=os.getenv("SOURCE_NAME","tata_1mg")
MIN_ROWS=int(os.getenv("MIN_ROWS","5000"))
MAX_ROWS=int(os.getenv("MAX_ROWS","10000"))
DELAY=float(os.getenv("REQUEST_DELAY_SECONDS","0.2"))
OUT=os.getenv("OUT_CSV","data/medicines_dataset.csv")
UA=os.getenv("SCRAPER_USER_AGENT","MedicineDatasetResearch/1.0")
HEAD={"User-Agent":UA,"Accept-Language":"en-IN,en;q=0.9"}

def clean(x):
    return re.sub(r"\s+", " ", str(x)).strip() if x is not None else None

def money(x):
    if x is None:
        return None
    s = str(x).strip()
    m = re.search(r"(?:₹|INR|Rs\.?|MRP)?\s*([\d,]+(?:\.\d+)?)", s, re.I)
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except (ValueError, AttributeError):
        return None

def jsonld(soup):
    out=[]
    for s in soup.find_all("script",type="application/ld+json"):
        try:
            x=json.loads(s.string or s.get_text())
            out += x if isinstance(x,list) else [x]
        except Exception: pass
    return out

def product(soup):
    for x in jsonld(soup):
        if isinstance(x,dict) and (x.get("@type")=="Product" or "Product" in (x.get("@type") if isinstance(x.get("@type"),list) else [])): return x
        for y in x.get("@graph",[]) if isinstance(x,dict) else []:
            if isinstance(y,dict) and y.get("@type")=="Product": return y
    return {}

def label(text,names):
    pattern = r"(?:%s)\s*[:\-]\s*([^|]{2,160})" % "|".join(map(re.escape, names))
    m = re.search(pattern, text, re.I)
    return clean(m.group(1)) if m else None

def extract_product(url,html):
    s=BeautifulSoup(html,"lxml"); p=product(s); text=clean(s.get_text(" ",strip=True)) or ""
    offers=p.get("offers",{}) if isinstance(p,dict) else {}; agg=p.get("aggregateRating",{}) if isinstance(p,dict) else {}
    brand=p.get("brand"); manufacturer=p.get("manufacturer")
    if isinstance(brand,dict): brand=brand.get("name")
    if isinstance(manufacturer,dict): manufacturer=manufacturer.get("name")
    name=clean(p.get("name")) or clean((s.find("meta",property="og:title") or {}).get("content"))
    if not name:
        h=s.find("h1"); name=clean(h.get_text(" ",strip=True)) if h else None
    if not name: return None
    price=money(offers.get("price") if isinstance(offers,dict) else None)
    if not price: return None
    rating=agg.get("ratingValue") if isinstance(agg,dict) else None
    reviews=agg.get("reviewCount") or agg.get("ratingCount") if isinstance(agg,dict) else None
    try: rating=float(rating) if rating is not None else None
    except: rating=None
    try: reviews=int(str(reviews).replace(",","")) if reviews is not None else None
    except: reviews=None
    brand=brand or label(text,["brand"]); manufacturer=manufacturer or label(text,["manufacturer","marketer"])
    rx=label(text,["prescription required","prescription"])
    if rx: rx="yes" if re.search(r"\b(yes|required)\b",rx,re.I) else "no"
    return {"name":name,"brand":clean(brand),"manufacturer":clean(manufacturer),"price_inr":price,
      "mrp_inr":money(label(text,["MRP","maximum retail price"])),"currency":clean(offers.get("priceCurrency") if isinstance(offers,dict) else None) or "INR",
      "rating":rating,"review_count":reviews,"category":label(text,["category","product type"]),
      "salt_composition":label(text,["salt composition","composition","active ingredient"]),
      "dosage_form":label(text,["dosage form","form"]),"pack_size":label(text,["pack size","pack of","quantity"]),
      "prescription_required":rx,"uses":label(text,["uses","indication"]),"source":SOURCE,"source_url":url,
      "scraped_at":datetime.now(timezone.utc).isoformat()}

def fetch(url):
    for n in range(6):
        try:
            r=requests.get(url,headers=HEAD,timeout=20)
            if r.status_code == 429:
                wait=min(30,2**n)
                print("page rate limited",url,"waiting",wait,flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            if "text/html" not in r.headers.get("content-type",""): raise ValueError("not HTML")
            return r.text
        except Exception:
            if n==5: raise
            time.sleep(1.0)

def discover():
    robots_url = urljoin(BASE, "/robots.txt")
    rr = requests.get(robots_url, headers=HEAD, timeout=15)
    rr.raise_for_status()
    rp = RobotFileParser()
    rp.parse(rr.text.splitlines())

    seeds = re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", rr.text)
    seeds = [s for s in seeds if urlparse(s).netloc.lower() == urlparse(BASE).netloc.lower()]

    candidates = []
    for seed in seeds:
        name = seed.lower()
        if "/drug" in name or "/medicine" in name or "/otc" in name or "/product" in name:
            candidates.append(seed)

    candidates.extend([
        urljoin(BASE, "/sitemap.xml"),
        urljoin(BASE, "/sitemap_index.xml"),
        urljoin(BASE, "/sitemap/sitemap.xml"),
    ])

    urls = []
    visited = set()

    def read_sitemap(sm, depth=0):
        if sm in visited or depth > 3 or len(urls) >= MAX_ROWS * 5:
            return
        visited.add(sm)
        try:
            x = requests.get(sm, headers=HEAD, timeout=15)
            if x.status_code == 404:
                return
            x.raise_for_status()
            root = ET.fromstring(x.content)
            root_type = root.tag.rsplit("}", 1)[-1]

            for node in root:
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "sitemap":
                    for child in node:
                        if child.tag.rsplit("}", 1)[-1] == "loc" and child.text:
                            read_sitemap(child.text.strip(), depth + 1)
                elif tag == "url":
                    for child in node:
                        if child.tag.rsplit("}", 1)[-1] == "loc" and child.text:
                            v = child.text.strip()
                            if urlparse(v).netloc.lower() != urlparse(BASE).netloc.lower():
                                continue
                            if not rp.can_fetch(UA, v):
                                continue
                            if SOURCE != "tata_1mg" or "/drugs/" in v or "/otc/" in v:
                                urls.append(v)
        except Exception as e:
            print("sitemap warning", sm, e)

    for seed in dict.fromkeys(candidates):
        read_sitemap(seed)

    return list(dict.fromkeys(urls))

def api_value(obj, keys):
    if not isinstance(obj, dict): return None
    wanted={k.lower() for k in keys}
    for k,v in obj.items():
        if str(k).lower() in wanted and v not in (None,"",[],{}): return v
    for v in obj.values():
        if isinstance(v, dict):
            got=api_value(v, keys)
            if got not in (None,"",[],{}): return got
    return None

def discover_api_rows():
    rows=[]; session=requests.Session()
    for letter in "abcdefghijklmnopqrstuvwxyz":
        for page in range(1,101):
            endpoint=urljoin(BASE, "/pharmacy_api_gateway/v4/drug_skus/by_prefix?prefix_term="+letter+"&page="+str(page)+"&per_page=100")
            try:
                payload=None
                for attempt in range(6):
                    r=session.get(endpoint,headers=HEAD,timeout=20)
                    if r.status_code == 429:
                        wait=min(30,2**attempt)
                        print("catalog rate limited",letter,page,"waiting",wait,flush=True)
                        time.sleep(wait)
                        continue
                    r.raise_for_status()
                    payload=r.json()
                    break
                if payload is None:
                    print("catalog warning",letter,page,"rate limit exhausted",flush=True); break
            except Exception as e:
                print("catalog warning",letter,page,e,flush=True); break
            skus=payload.get("data",{}).get("skus",[]) if isinstance(payload,dict) else []
            if not skus: break
            for sku in skus:
                if not isinstance(sku,dict): continue
                slug=api_value(sku,["slug","url"])
                url=str(slug) if slug and str(slug).startswith("http") else urljoin(BASE,str(slug or ""))
                if "/drugs/" not in url and "/otc/" not in url: continue
                name=api_value(sku,["name","product_name","display_name"])
                price=money(api_value(sku,["price","selling_price","discounted_price","sale_price"]))
                mrp=money(api_value(sku,["mrp","maximum_retail_price","list_price"]))
                if not name or not price or price<=0: continue
                rx=api_value(sku,["prescription_required","rx_required","is_prescription_required"])
                if isinstance(rx,bool): rx="yes" if rx else "no"
                rows.append({"name":clean(name),"brand":clean(api_value(sku,["brand","brand_name"])),
                    "manufacturer":clean(api_value(sku,["manufacturer","manufacturer_name","marketer"])),"price_inr":price,
                    "mrp_inr":mrp,"currency":"INR","rating":api_value(sku,["rating","rating_value"]),
                    "review_count":api_value(sku,["review_count","rating_count"]),
                    "category":clean(api_value(sku,["category","category_name"])),
                    "salt_composition":clean(api_value(sku,["salt_composition","salt","composition"])),
                    "dosage_form":clean(api_value(sku,["dosage_form","form"])),
                    "pack_size":clean(api_value(sku,["pack_size","pack","quantity"])),
                    "prescription_required":clean(rx),"uses":clean(api_value(sku,["uses","indications"])),
                    "source":SOURCE,"source_url":url,"scraped_at":datetime.now(timezone.utc).isoformat()})
                if len(rows)>=MAX_ROWS: return list({r["source_url"]:r for r in rows}.values())
            if page%5==0: print("catalog",letter,page,"rows",len(rows),flush=True)
            time.sleep(DELAY)
    return list({r["source_url"]:r for r in rows}.values())

def page_enrich(url):
    html=fetch(url)
    soup=BeautifulSoup(html,"lxml")
    text=clean(soup.get_text(" ",strip=True)) or ""
    out={}
    m=re.search(r"(?<!\d)([0-5](?:\.\d)?)\s+(\d[\d,]*)\s+ratings?\b",text[:12000],re.I)
    if m:
        out["rating"]=float(m.group(1)); out["review_count"]=int(m.group(2).replace(",",""))
    else:
        out["rating"]="Not rated"; out["review_count"]=0
    m=re.search(r"Therapeutic Class\s+(.+?)\s+Action Class\s+",text,re.I)
    if m: out["category"]=clean(m.group(1)).upper()
    if not out.get("category"):
        m=re.search(r"Action Class\s+(.+?)(?:Related lab tests|References|$)",text,re.I)
        if m: out["category"]=clean(m.group(1)).upper()
    m=re.search(r"Brand\s+(.+?)\s+(?:Composition|Contains|Marketer)\s+",text,re.I)
    if m: out["brand"]=clean(m.group(1))
    m=re.search(r"Contains\s+(.+?)\s+Marketer\s+",text,re.I)
    if m: out["salt_composition"]=clean(m.group(1))
    m=re.search(r"Marketer\s+(.+?)(?:\s+Storage\b|\s+Product information\b)",text,re.I)
    if m: out["manufacturer"]=clean(m.group(1))
    if re.search(r"Prescription\s+required",text[:5000],re.I): out["prescription_required"]="yes"
    m=re.search(r"Uses of .+?\s+(.+?)(?:\s+Benefits of|\s+Side effects of)",text,re.I)
    if m: out["uses"]=clean(m.group(1))
    m=re.search(r"\b(Tablet|Capsule|Syrup|Injection|Cream|Gel|Ointment|Drops?|Solution|Suspension|Powder|Spray|Inhaler|Patch|Granules|Lotion|Soap|Shampoo|Sachet|Kit|Gargle|Paste|Oil)\b",text[:3000],re.I)
    if m: out["dosage_form"]=m.group(1).title()
    m=re.search(r"\b(?:pack of|bottle of|box of|strip of|tube of|vial of)\s+([^,.]{1,40})",text[:5000],re.I)
    if m: out["pack_size"]=clean(m.group(1))
    m=re.search(r"MRP\s*₹?\s*([\d,]+(?:\.\d+)?)",text[:6000],re.I)
    if m: out["mrp_inr"]=money(m.group(1))
    return out

def fallback_category(name):
    n=(name or "").lower()
    rules=[
        (r"\b(insulin|metformin|glimepiride|sitagliptin|diabetes|gliptin)\b","DIABETES"),
        (r"\b(antibiotic|azithromycin|amoxicillin|cefixime|clavulanate)\b","ANTI INFECTIVES"),
        (r"\b(paracetamol|ibuprofen|diclofenac|pain|fever)\b","PAIN RELIEF"),
        (r"\b(antacid|pantoprazole|omeprazole|gastric|acidity)\b","GASTROINTESTINAL"),
        (r"\b(cetirizine|levocetirizine|fexofenadine|allergy)\b","ANTI ALLERGICS"),
        (r"\b(vitamin|calcium|multivitamin|iron|folic)\b","VITAMINS & MINERALS"),
        (r"\b(cancer|oncology|chemotherapy|bevacizumab|tamoxifen)\b","ANTI NEOPLASTICS"),
        (r"\b(amlodipine|telmisartan|losartan|cardiac|heart)\b","CARDIAC"),
        (r"\b(cough|cold|respiratory|asthma|inhaler)\b","RESPIRATORY"),
        (r"\b(derma|skin|cream|ointment|acne)\b","DERMATOLOGICALS")
    ]
    for pat,cat in rules:
        if re.search(pat,n): return cat
    return "GENERAL MEDICINE"

def main():
    rows=discover_api_rows()
    print("Catalog rows",len(rows),flush=True)
    if len(rows)<MIN_ROWS:
        raise SystemExit(f"Only {len(rows)} validated rows from catalog API; need {MIN_ROWS}.")
    enriched=[]
    for i,row in enumerate(rows[:MAX_ROWS],1):
        try:
            extra=page_enrich(row["source_url"])
            for k,v in extra.items():
                if v not in (None,""): row[k]=v
        except Exception as e:
            print("enrichment warning",i,row["source_url"],e,flush=True)
        if row.get("rating") in (None,""): row["rating"]="Not rated"
        if row.get("review_count") in (None,""): row["review_count"]=0
        if row.get("category") in (None,""): row["category"]=fallback_category(row.get("name"))
        for key in ["brand","salt_composition","dosage_form","uses"]:
            if row.get(key) in (None,""): row[key]="Not available"
        if row.get("mrp_inr") in (None,""): row["mrp_inr"]=row.get("price_inr")
        if row.get("prescription_required") in (None,""): row["prescription_required"]="no"
        if i%100==0: print("enriched",i,flush=True)
        time.sleep(DELAY)
        enriched.append(row)
    cols=["id","name","brand","manufacturer","price_inr","mrp_inr","currency","rating","review_count","category","salt_composition","dosage_form","pack_size","prescription_required","uses","source","source_url","scraped_at"]
    df=pd.DataFrame(enriched).drop_duplicates(subset=["source_url"]).head(MAX_ROWS).copy()
    df.insert(0,"id",[f"MED{i:06d}" for i in range(1,len(df)+1)])
    os.makedirs(os.path.dirname(OUT) or ".",exist_ok=True)
    df[cols].to_csv(OUT,index=False)
    print("Wrote",len(df),"rows",flush=True)

if __name__=="__main__": main()
