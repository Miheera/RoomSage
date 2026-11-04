# Catalog data sources and licensing

`products.json` is derived from the sources below and is distributed under
**CC BY-SA 4.0**, as the Flipkart source requires. This covers the data only, not the code.

## Flipkart Products (PromptCloud)

- Source: <https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products>
- License: CC BY-SA 4.0, as reported by the Kaggle dataset metadata
- Crawled: December 2015 to June 2016
- Changes: filtered to furniture and decor, mapped to RoomSage categories, parsed
  specifications into fields, added derived theme tags, removed near-duplicates

Prices are historic. Each record states its crawl date in `price_as_of`.
Product images remain the property of their owners and are referenced by URL, never copied.

## IKEA Saudi Arabia (TidyTuesday, 2020-11-03)

- Source: <https://github.com/rfordatascience/tidytuesday/tree/main/data/2020/2020-11-03>
- Origin: <https://www.kaggle.com/datasets/ahmedkallam/ikea-sa-furniture-web-scraping>
- License: TidyTuesday repository is CC0-1.0. The Kaggle origin lists "Other".
- Scraped: 2020-04-20
- Changes: mapped to RoomSage categories, read colour and material from the URL slug,
  added derived theme tags

Only factual fields are kept (name, size, price, link). The CC0 label cannot waive any
rights IKEA itself holds, so review this source before any commercial use. Prices are in
SAR and stay in `source_price`; `price_inr` is deliberately unknown for these records.

## Derived fields

`compatible_themes` is inferred by rule unless `theme_source` is `source_style` or
`manual`. `small_room_suitable` is computed from stated dimensions and is otherwise absent.
