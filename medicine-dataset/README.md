# 5K+ Real-World Medicine Dataset

This workflow builds a real web-scraped medicine/product dataset from permitted public pages. It does not fabricate rows or pad a smaller dataset.

## Fields

id, name, brand, manufacturer, price_inr, mrp_inr, currency, rating, review_count, category, salt_composition, dosage_form, pack_size, prescription_required, uses, source, source_url, scraped_at

## Source and compliance

The default source is Tata 1mg (https://www.1mg.com). The crawler reads robots.txt, follows sitemap declarations, stays on the configured host, rate-limits requests, retries transient failures, and never bypasses CAPTCHA/login/anti-bot controls.

The build fails when fewer than 5,000 valid products are obtained. That makes the 5,000+ requirement an actual data-quality gate.

## Run

python -m pip install -r requirements.txt

python -m unittest discover -s tests -v

python scripts/build_dataset.py --min-rows 5000

The GitHub Actions workflow also publishes the CSV as an artifact and can commit refreshed data.

Product prices and availability are dynamic. This is a data-engineering/research dataset, not medical or purchasing advice.
