#!/usr/bin/env node
const fs = require("fs");

const DEFAULT_GT_RADIAL_URLS = [
  "https://simpletire.com/brands/gt-radial-tires/maxtour-lx",
  "https://simpletire.com/brands/gt-radial-tires/adventuro-ht",
  "https://simpletire.com/brands/gt-radial-tires/adventuro-atx",
  "https://simpletire.com/brands/gt-radial-tires/maxclimate",
  "https://simpletire.com/brands/gt-radial-tires/champiro-sx2",
  "https://simpletire.com/brands/gt-radial-tires/champiro-hpy",
  "https://simpletire.com/brands/gt-radial-tires/maxmiler-pro",
  "https://simpletire.com/brands/gt-radial-tires/champiro-uhp-a-s",
  "https://simpletire.com/brands/gt-radial-tires/champiro-touring-a-s",
  "https://simpletire.com/brands/gt-radial-tires/maxtour-all-season",
  "https://simpletire.com/brands/gt-radial-tires/savero-ht2",
  "https://simpletire.com/brands/gt-radial-tires/adventuro-at3",
  "https://simpletire.com/brands/gt-radial-tires/komodo-mt-plus",
];

const USAGE = `
Usage:
  node simpletire-scrape.js <simpletire-product-url> [more-urls...] [--format json|csv]
  node simpletire-scrape.js --default-gt-radial [--format json|csv]
  node simpletire-scrape.js --file page.html [--url <product-url>] [--format json|csv]
`.trim();

// paste the rest of the script below this line

function parseArgs(argv) {
  const args = {
    format: "json",
    file: null,
    urls: [],
    useDefaultGtRadial: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--format") {
      args.format = argv[++i];
    } else if (arg === "--file") {
      args.file = argv[++i];
    } else if (arg === "--url") {
      args.urls.push(argv[++i]);
    } else if (arg === "--default-gt-radial") {
      args.useDefaultGtRadial = true;
    } else if (arg === "-h" || arg === "--help") {
      args.help = true;
    } else {
      args.urls.push(arg);
    }
  }

  if (!["json", "csv"].includes(args.format)) {
    throw new Error("--format must be either json or csv");
  }

  if (args.useDefaultGtRadial) {
    args.urls.push(...DEFAULT_GT_RADIAL_URLS);
  }

  args.urls = [...new Set(args.urls)];

  return args;
}

async function fetchHtml(url) {
  const response = await fetch(url, {
    headers: {
      "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "accept-language": "en-US,en;q=0.9",
      "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
    },
  });

  if (!response.ok) {
    throw new Error(`Fetch failed: HTTP ${response.status} ${response.statusText}`);
  }

  return response.text();
}

function decodeHtmlEntities(text) {
  return text
    .replace(/&quot;/g, "\"")
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function extractProductJsonLd(html) {
  const match = html.match(/<script[^>]*id=["']product-detail-product-group["'][^>]*>([\s\S]*?)<\/script>/i);
  if (!match) return null;

  try {
    return JSON.parse(decodeHtmlEntities(match[1].trim()));
  } catch {
    return null;
  }
}

function findFirstByKey(value, key) {
  const stack = [value];

  while (stack.length) {
    const current = stack.pop();
    if (!current || typeof current !== "object") continue;

    if (Object.prototype.hasOwnProperty.call(current, key)) {
      return current[key];
    }

    if (Array.isArray(current)) {
      for (let i = current.length - 1; i >= 0; i -= 1) stack.push(current[i]);
    } else {
      for (const child of Object.values(current)) stack.push(child);
    }
  }

  return undefined;
}

function extractNextFlightPayloads(html) {
  const payloads = [];
  const re = /self\.__next_f\.push\((\[[\s\S]*?\])\)<\/script>/g;

  for (const match of html.matchAll(re)) {
    try {
      const pushed = JSON.parse(match[1]);
      if (typeof pushed[1] === "string") payloads.push(pushed[1]);
    } catch {
      // Ignore unrelated or malformed script tags.
    }
  }

  return payloads;
}

function extractAvailableSizes(html) {
  for (const payload of extractNextFlightPayloads(html)) {
    if (!payload.includes("siteProductLineAvailableSizeList")) continue;

    const colon = payload.indexOf(":");
    if (colon === -1) continue;

    try {
      const tree = JSON.parse(payload.slice(colon + 1));
      const sizes = findFirstByKey(tree, "siteProductLineAvailableSizeList");
      if (Array.isArray(sizes) && sizes.length) return sizes;
    } catch {
      // Some React Server Component rows are not standalone JSON. Keep looking.
    }
  }

  return [];
}

function extractSiteProduct(html) {
  for (const payload of extractNextFlightPayloads(html)) {
    if (!payload.includes("siteProductLineAvailableSizeList")) continue;

    const colon = payload.indexOf(":");
    if (colon === -1) continue;

    try {
      const tree = JSON.parse(payload.slice(colon + 1));
      const siteProduct = findFirstByKey(tree, "siteProduct");
      if (siteProduct?.siteProductLineAvailableSizeList?.length) return siteProduct;
    } catch {
      // Some React Server Component rows are not standalone JSON. Keep looking.
    }
  }

  return null;
}

function centsToPrice(cents) {
  if (cents === null || cents === undefined || cents === "") return null;
  const amount = Number(cents) / 100;
  return Number.isFinite(amount) ? amount.toFixed(2) : null;
}

function specMap(specList) {
  const specs = {};
  for (const spec of specList || []) {
    if (spec && spec.label && !(spec.label in specs)) specs[spec.label] = spec.value ?? null;
  }
  return specs;
}

function productUrl(baseUrl, item) {
  const params = item.siteQueryParams || {};
  const url = new URL(baseUrl);

  if (params.mpn) url.hash = `mpn=${encodeURIComponent(params.mpn)}`;
  if (params.pageSource) url.hash += `${url.hash ? "&" : ""}pageSource=${encodeURIComponent(params.pageSource)}`;
  if (params.region) url.hash += `${url.hash ? "&" : ""}region=${encodeURIComponent(params.region)}`;
  if (params.tireSize) url.hash += `${url.hash ? "&" : ""}tireSize=${encodeURIComponent(params.tireSize)}`;

  return url.toString();
}

function normalizeRows(sizes, pageUrl, productGroup, siteProduct) {
  const productLine = siteProduct?.siteProductLine || {};
  const productName = productGroup?.name || productLine.name || null;
  const brand = productGroup?.brand?.name || productLine.brand?.label || null;

  return sizes.map((item) => {
    const specs = specMap(item.specList);
    // SimpleTire labels this value "Part Number" in the product details.
    const partNumber = item.partNumber || item.siteQueryParams?.mpn || null;

    return {
      sourceUrl: pageUrl,
      brand,
      productName,
      size: item.size || null,
      loadSpeedRating: item.loadSpeedRating || null,
      loadRange: item.loadRange || null,
      part_number: partNumber,
      // Keep the old name so existing consumers do not break.
      mpn: partNumber,
      itemId: item.siteQueryParams?.itemId || null,
      price: centsToPrice(item.priceInCents),
      priceInCents: item.priceInCents === undefined ? null : Number(item.priceInCents),
      quantity: item.quantity ?? null,
      rim: item.rim ?? null,
      isRunFlat: item.isRunFlat ?? null,
      bestPriceGuarantee: item.isBestPriceGuarantee ?? item.isBestPriceGuaranteed ?? null,
      url: productUrl(pageUrl, item),
      specs,
    };
  });
}

function csvEscape(value) {
  if (value === null || value === undefined) return "";
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, "\"\"")}"` : text;
}

function toCsv(rows) {
  const headers = [
    "sourceUrl",
    "brand",
    "productName",
    "size",
    "loadSpeedRating",
    "loadRange",
    "part_number",
    "mpn",
    "itemId",
    "price",
    "priceInCents",
    "quantity",
    "rim",
    "isRunFlat",
    "bestPriceGuarantee",
    "url",
    "specs",
  ];



  return [
    headers.join(","),
    ...rows.map((row) => headers.map((header) => csvEscape(row[header])).join(",")),
  ].join("\n");
}

async function scrapeUrl(pageUrl, htmlOverride) {
  const html = htmlOverride ?? await fetchHtml(pageUrl);
  const productGroup = extractProductJsonLd(html);
  const siteProduct = extractSiteProduct(html);
  const availableSizes = siteProduct?.siteProductLineAvailableSizeList || extractAvailableSizes(html);

  if (!availableSizes.length) {
    throw new Error(`Could not find siteProductLineAvailableSizeList in ${pageUrl}`);
  }

  return normalizeRows(availableSizes, pageUrl, productGroup, siteProduct);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(USAGE);
    return;
  }

  if (args.file && args.urls.length > 1) {
    throw new Error("--file can only be used with one --url value.");
  }

  if (!args.file && !args.urls.length) {
    throw new Error(`Missing URL.\n\n${USAGE}`);
  }

  const urls = args.urls.length ? args.urls : ["https://simpletire.com/"];
  const htmlOverride = args.file ? fs.readFileSync(args.file, "utf8") : null;
  const groups = [];

  for (const url of urls) {
    try {
      const rows = await scrapeUrl(url, htmlOverride);
      groups.push({ sourceUrl: url, count: rows.length, rows, error: null });
    } catch (error) {
      // A valid SimpleTire model page can have zero currently available
      // sizes. Report it, but do not discard results from every other model.
      console.error(`Warning: ${error.message}`);
      groups.push({ sourceUrl: url, count: 0, rows: [], error: error.message });
    }
  }

  const rows = groups.flatMap((group) => group.rows);

  if (args.format === "csv") {
    console.log(toCsv(rows));
  } else {
    console.log(JSON.stringify({
      sourceUrls: urls,
      scrapedAt: new Date().toISOString(),
      count: rows.length,
      products: groups.map(({ sourceUrl, count, error }) => ({ sourceUrl, count, error })),
      rows,
    }, null, 2));
  }
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exit(1);
});
