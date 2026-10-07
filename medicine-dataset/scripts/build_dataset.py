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
DELAY=float(os.getenv("REQUEST_DELAY_SECONDS","1.0"))
OUT=os.getenv("OUT_CSV","data/medicines_dataset.csv")
UA=os.getenv("SCRAPER_USER_AGENT","MedicineDatasetResearch/1.0")
HEAD={"User-Agent":UA,"Accept-Language":"en-IN,en;q=0.9"}

def clean(x):
    return re.sub(r"\\s+"," ",str(x)).strip() if x is not None else None

def money(x):
    if x is None: return None
    m=re.search(r"(?:₹|INR|Rs\\.?|MRP)\\s*([\\d,]+(?:\\.\\d+)?)",str(x),re.I)
    try: return float(m.group(1).replace(",","")) if m else None
    except ValueError: return None

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
    m=re.search(r"(?:%s)\\s*[:\\-]\\s*([^|]{2,160})"%"|".join(names),text,re.I)
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
    if rx: rx="yes" if re.search(r"\\b(yes|required)\\b",rx,re.I) else "no"
    return {"name":name,"brand":clean(brand),"manufacturer":clean(manufacturer),"price_inr":price,
      "mrp_inr":money(label(text,["MRP","maximum retail price"])),"currency":clean(offers.get("priceCurrency") if isinstance(offers,dict) else None) or "INR",
      "rating":rating,"review_count":reviews,"category":label(text,["category","product type"]),
      "salt_composition":label(text,["salt composition","composition","active ingredient"]),
      "dosage_form":label(text,["dosage form","form"]),"pack_size":label(text,["pack size","pack of","quantity"]),
      "prescription_required":rx,"uses":label(text,["uses","indication"]),"source":SOURCE,"source_url":url,
      "scraped_at":datetime.now(timezone.utc).isoformat()}

def fetch(url):
    for n in range(3):
        try:
            r=requests.get(url,headers=HEAD,timeout=30); r.raise_for_status()
            if "text/html" not in r.headers.get("content-type",""): raise ValueError("not HTML")
            return r.text
        except Exception:
            if n==2: raise
            time.sleep(2**n)

def discover():
    rr=requests.get(urljoin(BASE,"/robots.txt"),headers=HEAD,timeout=30); rr.raise_for_status()
    rp=RobotFileParser(); rp.parse(rr.text.splitlines())
    urls=[]
    for sm in re.findall(r"(?im)^\\s*Sitemap:\\s*(\\S+)",rr.text):
        try:
            x=requests.get(sm,headers=HEAD,timeout=30); x.raise_for_status(); root=ET.fromstring(x.content)
            for u in root.iter():
                if u.tag.rsplit("}",1)[-1]=="loc" and u.text:
                    v=u.text.strip()
                    if urlparse(v).netloc.lower()==urlparse(BASE).netloc.lower() and rp.can_fetch(UA,v):
                        if SOURCE!="tata_1mg" or "/drugs/" in v or "/otc/" in v: urls.append(v)
        except Exception as e: print("sitemap warning",e)
    return list(dict.fromkeys(urls))

def main():
    urls=discover(); print("Discovered",len(urls))
    rows=[]; seen=set()
    for i,u in enumerate(urls,1):
        try:
            row=extract_product(u,fetch(u))
            key=hashlib.sha256(u.encode()).hexdigest()[:16]
            if row and key not in seen: seen.add(key); rows.append(row)
        except Exception as e: print("skip",u,e)
        time.sleep(DELAY)
        if len(rows)>=MAX_ROWS: break
        if i%250==0: print("processed",i,"accepted",len(rows))
    if len(rows)<MIN_ROWS: raise SystemExit(f"Only {len(rows)} validated rows; need {MIN_ROWS}.")
    cols=["id","name","brand","manufacturer","price_inr","mrp_inr","currency","rating","review_count","category","salt_composition","dosage_form","pack_size","prescription_required","uses","source","source_url","scraped_at"]
    df=pd.DataFrame(rows); df.insert(0,"id",[f"MED{i:06d}" for i in range(1,len(df)+1)])
    os.makedirs(os.path.dirname(OUT) or ".",exist_ok=True); df[cols].to_csv(OUT,index=False)
    print("Wrote",len(df),"rows")

if __name__=="__main__": main()
