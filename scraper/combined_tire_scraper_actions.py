"""
Combined Tire Scraper — GitHub Actions Edition
===============================================

Scrapes:
    1. Giga Tires
    2. Priority Tire

Final output:
    tire_prices_YYYY-MM-DD.csv

Checkpoint outputs:
    giga_checkpoint.csv
    priority_checkpoint.csv

Designed for GitHub Actions.
"""

import asyncio
import datetime
import json
import re
import sys
from urllib.parse import urljoin

import nest_asyncio
import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeout,
)
from playwright_stealth import Stealth


nest_asyncio.apply()


# =============================================================================
# COMMON
# =============================================================================

COLUMN_ORDER = [
    "run_date",
    "source",
    "model",
    "size",
    "original_price",
    "easy_score",
    "reviews",
    "sku",
    "rating",
    "review_count",
    "in_stock",
    "price_per_tire",
    "total_4_tires",
    "warranty",
    "url",
    "error",
]


def save_checkpoint(rows, filename):
    """
    Save whatever has been collected so far.

    This is deliberately simple so that if GitHub Actions kills the process
    later, we still have partial data available.
    """
    if not rows:
        return

    df = pd.DataFrame(rows)
    df = df.reindex(columns=COLUMN_ORDER)

    for col in [
        "price_per_tire",
        "original_price",
        "total_4_tires",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.to_csv(filename, index=False)


# =============================================================================
# GIGA TIRES
# =============================================================================

GIGA_BASE_URL = "https://www.giga-tires.com"

# Two attempts is enough.
# Three retries across hundreds of products can destroy the runtime.
GIGA_MAX_RETRY = 2

GIGA_PAGE_TIMEOUT = 30_000
GIGA_PRICE_TIMEOUT = 10_000


GIGA_SEEDS = [
    {
        "model": "Maxtour LX",
        "url": f"{GIGA_BASE_URL}/235-45-18/gt-radial-tires/maxtour-lx/tirecode/AS122",
    },
    {
        "model": "Maxclimate",
        "url": f"{GIGA_BASE_URL}/225-40-18/gt-radial-tires/maxclimate/tirecode/100UA4532",
    },
    {
        "model": "Adventuro HT",
        "url": f"{GIGA_BASE_URL}/235-75-15/gt-radial-tires/adventuro-ht/tirecode/100UA3630",
    },
    {
        "model": "Adventuro ATX",
        "url": f"{GIGA_BASE_URL}/235-70-16/gt-radial-tires/adventuro-atx/tirecode/100UA3723",
    },
    {
        "model": "Champiro SX2",
        "url": f"{GIGA_BASE_URL}/225-45-17/gt-radial-tires/champiro-sx2/tirecode/B611",
    },
    {
        "model": "Champiro HPY",
        "url": f"{GIGA_BASE_URL}/255-35-18/gt-radial-tires/champiro-hpy/tirecode/B030",
    },
    {
        "model": "Maxmiler Pro",
        "url": f"{GIGA_BASE_URL}/185-60-15/gt-radial-tires/maxmiler-pro/tirecode/B623",
    },
    {
        "model": "Champiro UHP A/S",
        "url": f"{GIGA_BASE_URL}/195-55-15/gt-radial-tires/champiro-uhp-as/tirecode/100A2006",
    },
    {
        "model": "Champiro Touring A/S",
        "url": f"{GIGA_BASE_URL}/185-65-14/gt-radial-tires/champiro-touring-a-s/tirecode/B513",
    },
    {
        "model": "Maxtour All Season",
        "url": f"{GIGA_BASE_URL}/175-70-13/gt-radial-tires/maxtour-all-season/tirecode/AS065",
    },
    {
        "model": "Savero HT2",
        "url": f"{GIGA_BASE_URL}/215-70-15/gt-radial-tires/savero-ht2/tirecode/B452",
    },
    {
        "model": "Adventuro AT3",
        "url": f"{GIGA_BASE_URL}/235-75-15/gt-radial-tires/adventuro-at3/tirecode/AS087",
    },
    {
        "model": "Savero Komodo M/T Plus",
        "url": f"{GIGA_BASE_URL}/235-75-15/gt-radial-tires/savero-komodo-m-t-plus/tirecode/A289",
    },
]


def giga_empty(
    run_date,
    model,
    size,
    in_stock,
    url,
    error,
):
    return {
        "run_date": run_date,
        "source": "giga",
        "model": model,
        "size": size,
        "original_price": None,
        "easy_score": None,
        "reviews": None,
        "sku": None,
        "rating": None,
        "review_count": None,
        "in_stock": in_stock,
        "price_per_tire": None,
        "total_4_tires": None,
        "warranty": None,
        "url": url,
        "error": error,
    }


async def giga_load_seed(page, url):
    """
    Load a model seed page.

    IMPORTANT:
    We do NOT wait for a price here.

    The seed SKU itself may be unavailable while the model still has dozens
    of valid size variants in the dropdown.
    """
    for attempt in range(1, GIGA_MAX_RETRY + 1):
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=GIGA_PAGE_TIMEOUT,
            )

            # Wait for the size dropdown instead of waiting for a price.
            await page.wait_for_selector(
                "ul.j-dropdown-list li.j-dropdown-item",
                state="attached",
                timeout=15_000,
            )

            return True, ""

        except PlaywrightTimeout as e:
            if attempt < GIGA_MAX_RETRY:
                print(
                    f"  [seed retry {attempt}]",
                    end="",
                    flush=True,
                )
                await asyncio.sleep(2)
            else:
                return (
                    False,
                    f"Seed timeout after {GIGA_MAX_RETRY} attempts: {e}",
                )

        except Exception as e:
            return False, f"Seed page load error: {e}"


async def giga_load_product(page, url):
    """
    Load an individual IN-STOCK product page.

    Known OOS variants never reach this function.
    """
    for attempt in range(1, GIGA_MAX_RETRY + 1):
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=GIGA_PAGE_TIMEOUT,
            )

            await page.wait_for_selector(
                ".product-price--lg .product-price__current-price",
                state="attached",
                timeout=GIGA_PRICE_TIMEOUT,
            )

            return True, ""

        except PlaywrightTimeout as e:
            if attempt < GIGA_MAX_RETRY:
                print(
                    f"[retry {attempt}] ",
                    end="",
                    flush=True,
                )
                await asyncio.sleep(2)
            else:
                return (
                    False,
                    f"Timeout after {GIGA_MAX_RETRY} attempts: {e}",
                )

        except Exception as e:
            return False, f"Page load error: {e}"


async def giga_get_size_links(
    page,
    seed_url,
    model_name,
):
    print(f"\n[GIGA] ── {model_name}")
    print(f"  Loading seed: {seed_url}")

    ok, err = await giga_load_seed(
        page,
        seed_url,
    )

    if not ok:
        print(f"  ❌ Seed failed: {err}")
        return [], err

    try:
        links = await page.evaluate(
            """() => {
                const seen = new Set();
                const out = [];

                document
                    .querySelectorAll(
                        'ul.j-dropdown-list li.j-dropdown-item'
                    )
                    .forEach(li => {
                        const a = li.querySelector('a[href]');

                        if (!a) {
                            return;
                        }

                        const href = a.getAttribute('href');

                        if (
                            !href ||
                            href.startsWith('javascript') ||
                            seen.has(href)
                        ) {
                            return;
                        }

                        seen.add(href);

                        out.push({
                            size: a.innerText
                                .trim()
                                .replace('- Out of Stock', '')
                                .trim(),

                            href: href,

                            // Giga already tells us this in the dropdown.
                            in_stock:
                                !li.classList.contains(
                                    'not-orderable'
                                ),
                        });
                    });

                return out;
            }"""
        )

    except Exception as e:
        return [], f"Could not read size dropdown: {e}"

    print(
        f"  Found {len(links)} size variant(s)."
    )

    return links, ""


async def giga_scrape_page(
    page,
    run_date,
    model,
    size,
    url,
    in_stock,
):
    result = giga_empty(
        run_date,
        model,
        size,
        in_stock,
        url,
        "",
    )

    ok, err = await giga_load_product(
        page,
        url,
    )

    if not ok:
        result["error"] = err
        return result

    try:
        data = await page.evaluate(
            """() => {
                const txt = selector => {
                    const el =
                        document.querySelector(selector);

                    return el
                        ? el.innerText.trim()
                        : null;
                };

                return {
                    price: txt(
                        '.product-price--lg ' +
                        '.product-price__current-price'
                    ),

                    was: txt(
                        '.product-price--lg ' +
                        '.product-price__old-price .price'
                    ),

                    total4: txt(
                        '.product-price--md ' +
                        '.product-price__current-price'
                    ),

                    score: txt(
                        '.easyscore__rating'
                    ),

                    warranty: txt(
                        '.product-details-page__item-title-badge'
                    ),

                    reviews: txt(
                        '#product_just_stars .ind_cnt a'
                    ),
                };
            }"""
        )

        if data.get("price"):
            result["price_per_tire"] = (
                data["price"]
                .replace("$", "")
                .replace(",", "")
                .strip()
            )

        if data.get("was"):
            result["original_price"] = (
                data["was"]
                .replace("$", "")
                .replace(",", "")
                .strip()
            )

        if data.get("total4"):
            result["total_4_tires"] = (
                data["total4"]
                .replace("$", "")
                .replace(",", "")
                .strip()
            )

        if data.get("score"):
            result["easy_score"] = " ".join(
                data["score"].split()
            )

        if data.get("warranty"):
            result["warranty"] = " ".join(
                data["warranty"].split()
            )

        result["reviews"] = data.get("reviews")

        if not result["price_per_tire"]:
            result["error"] = "Price not found"

    except Exception as e:
        result["error"] = f"Parse error: {e}"

    return result


async def run_giga(run_date):
    results = []

    print("\n" + "═" * 60)
    print("  GIGA TIRES")
    print("═" * 60)

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={
                "width": 1366,
                "height": 768,
            },
            locale="en-US",
        )

        page = await ctx.new_page()

        # Kill some useless third-party requests.
        async def block_unnecessary(route):
            url = route.request.url.lower()

            blocked_terms = [
                "google-analytics",
                "googletagmanager",
                "doubleclick",
                "facebook",
                "hotjar",
                "attentive",
                "attn.tv",
            ]

            if any(
                term in url
                for term in blocked_terms
            ):
                await route.abort()
            else:
                await route.continue_()

        await page.route(
            "**/*",
            block_unnecessary,
        )

        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        for seed in GIGA_SEEDS:

            model_name = seed["model"]

            try:
                size_links, seed_err = (
                    await giga_get_size_links(
                        page,
                        seed["url"],
                        model_name,
                    )
                )

            except Exception as e:
                seed_err = f"Seed crashed: {e}"
                size_links = []

            if not size_links:
                results.append(
                    giga_empty(
                        run_date,
                        model_name,
                        None,
                        None,
                        seed["url"],
                        seed_err or "No sizes found",
                    )
                )

                save_checkpoint(
                    results,
                    "giga_checkpoint.csv",
                )

                continue

            total = len(size_links)

            for i, item in enumerate(
                size_links,
                1,
            ):
                size = item.get("size")

                url = urljoin(
                    GIGA_BASE_URL,
                    item.get("href"),
                )

                in_stock = item.get(
                    "in_stock"
                )

                print(
                    f"  "
                    f"[{i:02}/{total}] "
                    f"{str(size):15s}",
                    end="  ",
                    flush=True,
                )

                # ==========================================================
                # CRITICAL FIX
                # ==========================================================
                # If Giga's dropdown already says the SKU isn't orderable,
                # do NOT open the page and wait for a price that will never
                # appear.
                # ==========================================================

                if in_stock is False:

                    data = giga_empty(
                        run_date,
                        model_name,
                        size,
                        False,
                        url,
                        "Out of stock",
                    )

                    results.append(data)

                    print(
                        f"{'$N/A':>8}  OOS"
                    )

                    continue

                try:
                    data = await giga_scrape_page(
                        page,
                        run_date,
                        model_name,
                        size,
                        url,
                        in_stock,
                    )

                except Exception as e:
                    data = giga_empty(
                        run_date,
                        model_name,
                        size,
                        in_stock,
                        url,
                        f"Crashed: {e}",
                    )

                results.append(data)

                price = (
                    f"${data.get('price_per_tire')}"
                    if data.get("price_per_tire")
                    else "$N/A"
                )

                stock = (
                    "In Stock"
                    if in_stock
                    else "OOS"
                )

                err = (
                    f"  !! {data.get('error')}"
                    if data.get("error")
                    else ""
                )

                print(
                    f"{price:>8}  "
                    f"{stock}"
                    f"{err}"
                )

                await asyncio.sleep(0.35)

            # --------------------------------------------------------------
            # CHECKPOINT AFTER EVERY MODEL
            # --------------------------------------------------------------
            save_checkpoint(
                results,
                "giga_checkpoint.csv",
            )

            print(
                f"  💾 Giga checkpoint saved "
                f"({len(results)} rows total)"
            )

        await browser.close()

    print(
        f"\n  Giga: "
        f"{len(results)} rows collected."
    )

    return results


# =============================================================================
# PRIORITY TIRE
# =============================================================================

PRIORITY_BASE_URL = (
    "https://www.prioritytire.com"
)

PRIORITY_MAX_RETRY = 2


PRIORITY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


PRIORITY_SEEDS = [
    {
        "model": "Maxtour LX",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "maxtour-lx/"
            "215-45r17-87v-7388"
        ),
    },
    {
        "model": "Maxclimate",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "gt-radial-maxclimate/"
            "225-40r18-92v-xl-173817"
        ),
    },
    {
        "model": "Adventuro HT",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "adventuro-ht/"
            "235-85r16-120-116s-e-10-ply-14309"
        ),
    },
    {
        "model": "Adventuro ATX",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "adventuro-atx/"
            "235-75r15-108s-xl-19048"
        ),
    },
    {
        "model": "Champiro SX2",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "champiro-sx2/"
            "235-45r17-94w-zr-11644"
        ),
    },
    {
        "model": "Champiro HPY",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "champiro-hpy/"
            "225-40r19-93y-xl-zr-63955"
        ),
    },
    {
        "model": "Champiro UHP A/S",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "champiro-uhp-a-s/"
            "215-55r17-94v-43619"
        ),
    },
    {
        "model": "Maxmiler Pro",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "maxmiler-pro/"
            "245-75r16-120-116q-e-10-ply-3623"
        ),
    },
    {
        "model": "Champiro Touring A/S",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "champiro-touring-a-s/"
            "205-55r16-91h-43691"
        ),
    },
    {
        "model": "Maxtour All Season",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "maxtour-all-season/"
            "185-65r15-88t-52698"
        ),
    },
    {
        "model": "Savero HT2",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "savero-ht2/"
            "275-55r20-111h-66171"
        ),
    },
    {
        "model": "Adventuro AT3",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "adventuro-at3/"
            "235-85r16-120-116s-e-10-ply-43734"
        ),
    },
    {
        "model": "Savero Komodo M/T Plus",
        "url": (
            f"{PRIORITY_BASE_URL}"
            "/by-brand/gt-radial-tires/"
            "savero-komodo-m-t-plus/"
            "235-75r15-104-101q-c-6-ply-53015"
        ),
    },
]


def _priority_product_path(url):
    parts = (
        url
        .replace(
            PRIORITY_BASE_URL,
            "",
        )
        .strip("/")
        .split("/")
    )

    return "/" + "/".join(
        parts[:3]
    )


async def priority_get_size_urls(
    page,
    seed,
):
    model_name = seed["model"]
    seed_url = seed["url"]

    seed_product_path = (
        _priority_product_path(seed_url)
    )

    print(
        f"\n[PRIORITY] ── "
        f"{model_name}"
    )

    print(
        f"  Loading seed: "
        f"{seed_url}"
    )

    await page.goto(
        seed_url,
        wait_until="domcontentloaded",
        timeout=30_000,
    )

    await page.wait_for_selector(
        "script#__NEXT_DATA__",
        state="attached",
        timeout=15_000,
    )

    raw = await page.evaluate(
        """() => {
            const el =
                document.getElementById(
                    '__NEXT_DATA__'
                );

            return el
                ? el.textContent
                : null;
        }"""
    )

    if not raw:
        print(
            "  ⚠️ __NEXT_DATA__ "
            "not found — skipping."
        )
        return []

    nd = json.loads(raw)

    apollo = (
        nd["props"]
        ["pageProps"]
        ["apolloState"]
    )

    config_product = next(
        (
            value
            for key, value
            in apollo.items()
            if (
                key.startswith(
                    "ConfigurableProduct:"
                )
                and isinstance(
                    value,
                    dict,
                )
                and "variants" in value
            )
        ),
        None,
    )

    if not config_product:
        print(
            "  ⚠️ No ConfigurableProduct "
            "found — skipping."
        )
        return []

    print(
        f"  Found "
        f"{len(config_product['variants'])} "
        f"variants in apolloState"
    )

    variants = []

    for variant in config_product[
        "variants"
    ]:
        if not isinstance(
            variant,
            dict,
        ):
            continue

        size_label = None

        for attr in (
            variant.get(
                "attributes"
            )
            or []
        ):
            if (
                isinstance(attr, dict)
                and attr.get("__ref")
            ):
                attr = apollo.get(
                    attr["__ref"],
                    {},
                )

            label = (
                attr.get("label")
                or attr.get(
                    "store_label"
                )
            )

            if label:
                size_label = label
                break

        prod_ref = (
            variant.get("product")
            or {}
        )

        if (
            isinstance(prod_ref, dict)
            and prod_ref.get("__ref")
        ):
            simple = apollo.get(
                prod_ref["__ref"],
                {},
            )
        else:
            simple = prod_ref

        sku_id = (
            simple.get("id")
            or simple.get("uid")
        )

        slug = None

        for rewrite in (
            simple.get("url_rewrites")
            or []
        ):
            if (
                isinstance(
                    rewrite,
                    dict,
                )
                and rewrite.get(
                    "__ref"
                )
            ):
                rewrite = apollo.get(
                    rewrite["__ref"],
                    {},
                )

            if (
                isinstance(
                    rewrite,
                    dict,
                )
                and rewrite.get("url")
            ):
                slug = (
                    "/"
                    + rewrite["url"]
                    .lstrip("/")
                )
                break

        if (
            not slug
            and size_label
            and sku_id
        ):
            size_slug = re.sub(
                r"-{2,}",
                "-",
                re.sub(
                    r"[^a-z0-9]",
                    "-",
                    size_label.lower(),
                ),
            ).strip("-")

            slug = (
                f"{seed_product_path}/"
                f"{size_slug}-"
                f"{sku_id}"
            )

        if slug:
            variants.append(
                {
                    "model": model_name,
                    "size": (
                        size_label
                        or slug.split(
                            "/"
                        )[-1]
                    ),
                    "url": (
                        PRIORITY_BASE_URL
                        + slug
                    ),
                    "sku": (
                        str(sku_id)
                        if sku_id
                        else None
                    ),
                    "in_stock": (
                        simple.get(
                            "stock_status"
                        )
                        == "IN_STOCK"
                        if "stock_status"
                        in simple
                        else None
                    ),
                }
            )

    seen = set()
    deduped = []

    for variant in variants:

        if variant["url"] in seen:
            continue

        seen.add(
            variant["url"]
        )

        deduped.append(
            variant
        )

    print(
        f"  → {len(deduped)} "
        f"unique sizes ready to scrape"
    )

    return deduped


def priority_parse_next_data(
    html,
    fallback_size,
    url=None,
    sku=None,
):
    result = {
        "price_per_tire": None,
        "total_4_tires": None,
        "rating": None,
        "review_count": None,
        "warranty": None,
        "in_stock": None,
        "size": fallback_size,
    }

    match = re.search(
        (
            r'<script '
            r'id="__NEXT_DATA__"'
            r'[^>]*>'
            r'(.*?)'
            r'</script>'
        ),
        html,
        re.DOTALL,
    )

    if not match:
        return result

    try:
        nd = json.loads(
            match.group(1)
        )

        apollo = (
            nd["props"]
            ["pageProps"]
            ["apolloState"]
        )

        # The Apollo SimpleProduct ID discovered during the seed
        # crawl is more reliable than trying to guess from the URL.
        target_id = (
            str(sku)
            if sku is not None
            else None
        )

        if (
            target_id is None
            and url
        ):
            last_segment = (
                url.rstrip("/")
                .split("/")[-1]
            )

            id_match = re.search(
                r"-(\d+)$",
                last_segment,
            )

            if id_match:
                target_id = (
                    id_match.group(1)
                )

        simple = None

        for key, value in (
            apollo.items()
        ):
            if (
                key.startswith(
                    "SimpleProduct:"
                )
                and isinstance(
                    value,
                    dict,
                )
                and "price_range"
                in value
            ):
                product_id = (
                    value.get("id")
                    or value.get("uid")
                )

                if (
                    target_id is None
                    or str(
                        product_id
                    )
                    == target_id
                ):
                    simple = value
                    break

        config = next(
            (
                value
                for key, value
                in apollo.items()
                if (
                    key.startswith(
                        "ConfigurableProduct:"
                    )
                    and isinstance(
                        value,
                        dict,
                    )
                )
            ),
            None,
        )

        if simple:
            try:
                price_range = (
                    simple["price_range"]
                )

                if (
                    isinstance(
                        price_range,
                        dict,
                    )
                    and price_range.get(
                        "__ref"
                    )
                ):
                    price_range = (
                        apollo.get(
                            price_range[
                                "__ref"
                            ],
                            {},
                        )
                    )

                minimum_price = (
                    price_range.get(
                        "minimum_price"
                    )
                    or {}
                )

                if (
                    isinstance(
                        minimum_price,
                        dict,
                    )
                    and minimum_price.get(
                        "__ref"
                    )
                ):
                    minimum_price = (
                        apollo.get(
                            minimum_price[
                                "__ref"
                            ],
                            {},
                        )
                    )

                final_price = (
                    minimum_price.get(
                        "final_price"
                    )
                    or {}
                )

                if (
                    isinstance(
                        final_price,
                        dict,
                    )
                    and final_price.get(
                        "__ref"
                    )
                ):
                    final_price = (
                        apollo.get(
                            final_price[
                                "__ref"
                            ],
                            {},
                        )
                    )

                price_value = (
                    final_price.get(
                        "value"
                    )
                )

                if price_value is not None:
                    result[
                        "price_per_tire"
                    ] = float(
                        price_value
                    )

                    result[
                        "total_4_tires"
                    ] = round(
                        float(
                            price_value
                        )
                        * 4,
                        2,
                    )

            except Exception:
                pass

            if simple.get(
                "stock_status"
            ):
                result[
                    "in_stock"
                ] = (
                    simple[
                        "stock_status"
                    ]
                    == "IN_STOCK"
                )

        if config:
            rating_data = (
                config.get(
                    "productRating"
                )
                or {}
            )

            if (
                isinstance(
                    rating_data,
                    dict,
                )
                and rating_data.get(
                    "__ref"
                )
            ):
                rating_data = (
                    apollo.get(
                        rating_data[
                            "__ref"
                        ],
                        {},
                    )
                )

            rating_value = (
                rating_data.get(
                    "rating_summary"
                )
                or config.get(
                    "rating_summary"
                )
            )

            if rating_value:
                try:
                    result[
                        "rating"
                    ] = (
                        f"{float(rating_value) / 20:.1f}/5"
                    )

                except Exception:
                    result[
                        "rating"
                    ] = str(
                        rating_value
                    )

            if (
                config.get(
                    "review_count"
                )
                is not None
            ):
                result[
                    "review_count"
                ] = str(
                    config[
                        "review_count"
                    ]
                )

            if config.get(
                "treadlife_warranty_text"
            ):
                result[
                    "warranty"
                ] = config[
                    "treadlife_warranty_text"
                ]

        soup = BeautifulSoup(
            html,
            "lxml",
        )

        if (
            result[
                "price_per_tire"
            ]
            is None
        ):
            el = soup.select_one(
                ".ProductPagePrice-finalPrice span"
            )

            if el:
                try:
                    price = float(
                        el
                        .get_text(
                            strip=True
                        )
                        .replace(
                            "$",
                            "",
                        )
                        .replace(
                            ",",
                            "",
                        )
                    )

                    result[
                        "price_per_tire"
                    ] = price

                    result[
                        "total_4_tires"
                    ] = round(
                        price * 4,
                        2,
                    )

                except ValueError:
                    pass

        if (
            result["in_stock"]
            is None
        ):
            page_text = " ".join(
                soup.stripped_strings
            )

            if re.search(
                r"\bIn Stock\b",
                page_text,
                re.IGNORECASE,
            ):
                result[
                    "in_stock"
                ] = True

            elif re.search(
                r"\bOut of Stock\b",
                page_text,
                re.IGNORECASE,
            ):
                result[
                    "in_stock"
                ] = False

    except Exception as e:
        result[
            "parse_error"
        ] = str(e)

    return result


async def priority_fetch_one(
    page,
    item,
    run_date,
    index,
    total,
):
    size = item["size"]
    url = item["url"]

    print(
        f"  Loading "
        f"[{index:03}/{total}] "
        f"{item.get('model'):22s}  "
        f"{str(size):28s}",
        flush=True,
    )

    result = {
        "run_date": run_date,
        "source": "priority",
        "model": item.get("model"),
        "size": size,
        "original_price": None,
        "easy_score": None,
        "reviews": None,
        "sku": item.get("sku"),
        "rating": None,
        "review_count": None,
        "in_stock": item.get(
            "in_stock"
        ),
        "price_per_tire": None,
        "total_4_tires": None,
        "warranty": None,
        "url": url,
        "error": "",
    }

    for attempt in range(
        1,
        PRIORITY_MAX_RETRY + 1,
    ):
        try:
            await page.goto(
                url,
                wait_until=(
                    "domcontentloaded"
                ),
                timeout=30_000,
            )

            await page.wait_for_selector(
                "script#__NEXT_DATA__",
                state="attached",
                timeout=10_000,
            )

            selected_id = (
                await page.evaluate(
                    """() => {
                        const el =
                            document.getElementById(
                                '__NEXT_DATA__'
                            );

                        if (!el) {
                            return null;
                        }

                        const nd =
                            JSON.parse(
                                el.textContent
                            );

                        const apollo =
                            nd?.props
                              ?.pageProps
                              ?.apolloState
                            || {};

                        for (
                            const [key, value]
                            of Object.entries(apollo)
                        ) {
                            if (
                                key.startsWith(
                                    'SimpleProduct:'
                                )
                                &&
                                value
                                &&
                                typeof value
                                    === 'object'
                                &&
                                value.price_range
                            ) {
                                return String(
                                    value.id
                                    ||
                                    value.uid
                                    ||
                                    ''
                                );
                            }
                        }

                        return null;
                    }"""
                )
            )

            expected_id = (
                str(
                    item.get("sku")
                )
                if item.get("sku")
                else None
            )

            result["url"] = (
                page.url
            )

            # Some obsolete variants redirect to another
            # product. Don't save that product under the
            # requested size.
            if (
                expected_id
                and selected_id
                and selected_id
                != expected_id
            ):
                result[
                    "in_stock"
                ] = False

                result[
                    "error"
                ] = (
                    f"Variant "
                    f"{expected_id} "
                    f"redirected to "
                    f"{selected_id}; "
                    f"skipped"
                )

                break

            html = (
                await page.content()
            )

            parsed = (
                priority_parse_next_data(
                    html,
                    size,
                    url=page.url,
                    sku=expected_id,
                )
            )

            result.update(parsed)

            if (
                result.get(
                    "price_per_tire"
                )
                is None
            ):
                result[
                    "error"
                ] = (
                    "Price not found "
                    "after browser navigation"
                )

            break

        except PlaywrightTimeout as e:

            if (
                attempt
                < PRIORITY_MAX_RETRY
            ):
                print(
                    f"    retry "
                    f"{attempt}..."
                )

                await asyncio.sleep(
                    2 * attempt
                )

            else:
                result[
                    "error"
                ] = (
                    f"Timeout after "
                    f"{PRIORITY_MAX_RETRY} "
                    f"attempts: {e}"
                )

        except Exception as e:
            result[
                "error"
            ] = (
                f"Browser scrape "
                f"error: {e}"
            )

            break

    price = (
        f"${result.get('price_per_tire')}"
        if result.get(
            "price_per_tire"
        )
        else "$N/A"
    )

    stock = (
        "In Stock"
        if result.get(
            "in_stock"
        )
        else "OOS"
    )

    err = (
        f"  !! {result['error']}"
        if result.get("error")
        else ""
    )

    print(
        f"  "
        f"[{index:03}/{total}] "
        f"{result['model']:22s}  "
        f"{str(result['size']):28s}  "
        f"{price:>10}  "
        f"{stock}"
        f"{err}"
    )

    return result


async def run_priority(
    run_date,
):
    all_size_links = []

    print(
        "\n"
        + "═" * 60
    )

    print(
        "  PRIORITY TIRE"
    )

    print(
        "═" * 60
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        ctx = await browser.new_context(
            user_agent=(
                PRIORITY_HEADERS[
                    "User-Agent"
                ]
            )
        )

        discovery_page = (
            await ctx.new_page()
        )

        detail_page = (
            await ctx.new_page()
        )

        # Block known third-party junk.
        async def block_priority(
            route,
        ):
            url = (
                route.request.url
                .lower()
            )

            blocked = [
                "attentive",
                "attn.tv",
                "google-analytics",
                "googletagmanager",
                "doubleclick",
                "facebook",
            ]

            if any(
                term in url
                for term in blocked
            ):
                await route.abort()
            else:
                await route.continue_()

        await discovery_page.route(
            "**/*",
            block_priority,
        )

        await detail_page.route(
            "**/*",
            block_priority,
        )

        # --------------------------------------------------------------
        # DISCOVER ALL VARIANTS
        # --------------------------------------------------------------

        for seed in PRIORITY_SEEDS:

            try:
                links = (
                    await priority_get_size_urls(
                        discovery_page,
                        seed,
                    )
                )

                all_size_links.extend(
                    links
                )

            except Exception as e:
                print(
                    f"  ❌ Priority "
                    f"seed failed for "
                    f"{seed['model']}: "
                    f"{e}"
                )

        total = len(
            all_size_links
        )

        print(
            f"\n  Total Priority "
            f"sizes: {total}\n"
        )

        results = []

        # --------------------------------------------------------------
        # SCRAPE VARIANTS
        # --------------------------------------------------------------

        for i, item in enumerate(
            all_size_links,
            1,
        ):
            try:
                result = (
                    await priority_fetch_one(
                        detail_page,
                        item,
                        run_date,
                        i,
                        total,
                    )
                )

            except Exception as e:

                result = {
                    "run_date": run_date,
                    "source": "priority",
                    "model": item.get(
                        "model"
                    ),
                    "size": item.get(
                        "size"
                    ),
                    "original_price": None,
                    "easy_score": None,
                    "reviews": None,
                    "sku": item.get(
                        "sku"
                    ),
                    "rating": None,
                    "review_count": None,
                    "in_stock": (
                        item.get(
                            "in_stock"
                        )
                    ),
                    "price_per_tire": None,
                    "total_4_tires": None,
                    "warranty": None,
                    "url": item.get(
                        "url"
                    ),
                    "error": (
                        f"Unhandled "
                        f"scrape error: "
                        f"{e}"
                    ),
                }

            results.append(
                result
            )

            # Save every 25 records.
            if (
                i % 25 == 0
                or i == total
            ):
                save_checkpoint(
                    results,
                    "priority_checkpoint.csv",
                )

                print(
                    f"  💾 Priority "
                    f"checkpoint saved "
                    f"({len(results)} rows)"
                )

            await asyncio.sleep(
                0.3
            )

        await browser.close()

    print(
        f"\n  Priority: "
        f"{len(results)} "
        f"rows collected."
    )

    return results


# =============================================================================
# MAIN
# =============================================================================

async def main():

    now_utc = (
        datetime.datetime.now(
            datetime.UTC
        )
    )

    run_date = (
        now_utc.strftime(
            "%Y-%m-%d "
            "%H:%M UTC"
        )
    )

    date_str = (
        now_utc.strftime(
            "%Y-%m-%d"
        )
    )

    print(
        "\n"
        "════════════════════════════════════════════════════════════"
    )

    print(
        "  TIRE PRICE SCRAPER"
    )

    print(
        f"  Run time: {run_date}"
    )

    print(
        "════════════════════════════════════════════════════════════\n"
    )

    # -----------------------------------------------------------------
    # GIGA
    # -----------------------------------------------------------------

    try:
        giga_results = (
            await run_giga(
                run_date
            )
        )

    except Exception as e:
        print(
            f"\n❌ Giga scraper "
            f"crashed: {e}"
        )

        giga_results = []

    # Always save whatever Giga gave us.
    save_checkpoint(
        giga_results,
        "giga_checkpoint.csv",
    )

    # -----------------------------------------------------------------
    # PRIORITY
    # -----------------------------------------------------------------

    try:
        priority_results = (
            await run_priority(
                run_date
            )
        )

    except Exception as e:
        print(
            f"\n❌ Priority scraper "
            f"crashed: {e}"
        )

        priority_results = []

    save_checkpoint(
        priority_results,
        "priority_checkpoint.csv",
    )

    # -----------------------------------------------------------------
    # COMBINE
    # -----------------------------------------------------------------

    all_results = (
        giga_results
        + priority_results
    )

    if not all_results:
        print(
            "\n❌ No rows were "
            "collected from either "
            "scraper."
        )

        sys.exit(1)

    df = pd.DataFrame(
        all_results
    )

    for col in [
        "price_per_tire",
        "original_price",
        "total_4_tires",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.reindex(
        columns=COLUMN_ORDER
    )

    out_file = (
        f"tire_prices_"
        f"{date_str}.csv"
    )

    df.to_csv(
        out_file,
        index=False,
    )

    # -----------------------------------------------------------------
    # REPORT
    # -----------------------------------------------------------------

    print(
        "\n"
        "════════════════════════════════════════════════════════════"
    )

    print(
        "  SCRAPE COMPLETE"
    )

    print(
        "════════════════════════════════════════════════════════════"
    )

    print(
        f"\n✅ Saved → "
        f"{out_file}"
    )

    print(
        f"   Total rows: "
        f"{len(df)}"
    )

    print(
        f"   Giga: "
        f"{len(giga_results)} rows"
    )

    print(
        f"   Priority: "
        f"{len(priority_results)} rows"
    )

    errors = df[
        df["error"].notna()
        & (
            df["error"]
            != ""
        )
    ]

    priced = df[
        df[
            "price_per_tire"
        ].notna()
    ]

    print(
        f"   Rows with price: "
        f"{len(priced)}"
    )

    print(
        f"   Rows with errors/OOS: "
        f"{len(errors)}"
    )

    if not errors.empty:
        print(
            "\n⚠️ Some rows had "
            "errors or were unavailable."
        )

        print(
            "   Check the CSV "
            "'error' column."
        )

    # Only fail GitHub Actions if literally nothing useful was scraped.
    if priced.empty:
        print(
            "\n❌ No prices were "
            "successfully scraped."
        )

        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
