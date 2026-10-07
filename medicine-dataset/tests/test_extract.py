import unittest
from scripts.build_dataset import extract_product

HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Example 500 Tablet","brand":{"@type":"Brand","name":"Example"},"manufacturer":{"@type":"Organization","name":"Example Pharma"},"offers":{"@type":"Offer","price":"125.50","priceCurrency":"INR"},"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.3","reviewCount":"127"}}
</script></head><body><h1>Example 500 Tablet</h1></body></html>
"""

class TestExtract(unittest.TestCase):
    def test_product_jsonld(self):
        row = extract_product("https://www.1mg.com/drugs/example-1", HTML)
        self.assertEqual(row["name"], "Example 500 Tablet")
        self.assertEqual(row["brand"], "Example")
        self.assertEqual(row["manufacturer"], "Example Pharma")
        self.assertEqual(row["price_inr"], 125.5)
        self.assertEqual(row["rating"], 4.3)
        self.assertEqual(row["review_count"], 127)

if __name__ == "__main__":
    unittest.main()
