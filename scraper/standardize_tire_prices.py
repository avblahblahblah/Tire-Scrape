import datetime
import glob
import os
import re

import pandas as pd


TODAY = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def clean_money(value):
    if pd.isna(value):
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_bool(value):
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ["true", "1", "yes", "y", "in stock"]


def normalize_model(value):
    if pd.isna(value):
        return None

    text = str(value).strip()
    replacements = {
        "gt radial maxtour lx": "Maxtour LX",
        "maxtour lx": "Maxtour LX",
        "gt radial maxclimate": "Maxclimate",
        "maxclimate": "Maxclimate",
        "maxclimate tire": "Maxclimate",
        "gt radial adventuro ht": "Adventuro HT",
        "adventuro ht": "Adventuro HT",
        "gt radial adventuro atx": "Adventuro ATX",
        "adventuro atx": "Adventuro ATX",
        "gt radial champiro sx2": "Champiro SX2",
        "champiro sx2": "Champiro SX2",
        "gt radial champiro hpy": "Champiro HPY",
        "champiro hpy": "Champiro HPY",
        "gt radial maxmiler pro": "Maxmiler Pro",
        "maxmiler pro": "Maxmiler Pro",
        "gt radial champiro uhp a/s": "Champiro UHP A/S",
        "gt radial champiro uhp as": "Champiro UHP A/S",
        "champiro uhp a/s": "Champiro UHP A/S",
        "champiro uhp as": "Champiro UHP A/S",
        "gt radial champiro touring a/s": "Champiro Touring A/S",
        "gt radial champiro touring as": "Champiro Touring A/S",
        "champiro touring a/s": "Champiro Touring A/S",
        "champiro touring as": "Champiro Touring A/S",
        "gt radial maxtour all season": "Maxtour All Season",
        "maxtour all season": "Maxtour All Season",
        "gt radial savero ht2": "Savero HT2",
        "savero ht2": "Savero HT2",
        "gt radial adventuro at3": "Adventuro AT3",
        "adventuro at3": "Adventuro AT3",
        "gt radial savero komodo m/t plus": "Savero Komodo M/T Plus",
        "gt radial savero komodo mt plus": "Savero Komodo M/T Plus",
        "savero komodo m/t plus": "Savero Komodo M/T Plus",
        "savero komodo mt plus": "Savero Komodo M/T Plus",
        "gt radial komodo m/t plus": "Savero Komodo M/T Plus",
        "gt radial komodo mt plus": "Savero Komodo M/T Plus",
        "komodo m/t plus": "Savero Komodo M/T Plus",
        "komodo mt plus": "Savero Komodo M/T Plus",
    }

    key = re.sub(r"\s+", " ", text.lower().replace("-", " ")).strip()
    return replacements.get(key, text)


def first_available(df, columns):
    """Coalesce compatible columns while accepting older scraper files."""
    result = pd.Series(pd.NA, index=df.index, dtype="object")
    for column in columns:
        if column in df.columns:
            values = df[column].replace(r"^\s*$", pd.NA, regex=True)
            result = result.fillna(values)
    return result


def raw_size_from_size(size, website=None):
    if pd.isna(size):
        return None

    text = str(size).upper().strip()

    # Handles:
    # 195/50ZR15  -> 195/50R15
    # 245/75R16   -> 245/75R16
    # LT245/75R16 -> LT245/75R16
    # 185/60R15C  -> 185/60R15C
    metric_match = re.search(
        r"\b(LT|P)?(\d{3}/\d{2})(?:Z?R)(\d{2})(C)?\b",
        text,
    )

    if metric_match:
        prefix = metric_match.group(1) or ""
        commercial_suffix = metric_match.group(4) or ""

        raw_size = (
            f"{prefix}"
            f"{metric_match.group(2)}"
            f"R{metric_match.group(3)}"
            f"{commercial_suffix}"
        )

        # P-prefix is not retained in RAW SIZE
        if raw_size.startswith("P"):
            raw_size = raw_size[1:]

        # Priority identifies LT tires using a dual load index:
        # 245/75R16 120/116S E (10 Ply)
        if str(website).strip().lower() == "priority":
            remaining_text = text[metric_match.end():]

            if re.search(r"\b\d{3}/\d{3}[A-Z]\b", remaining_text):
                if not raw_size.startswith("LT"):
                    raw_size = f"LT{raw_size}"

        return raw_size

    # Flotation sizes such as:
    # 31X10.50R15LT
    # 33X12.50R15LT
    # 35X12.50R20
    flotation_match = re.search(
        r"\b(\d{2,3}X\d{1,2}\.\d{2}R\d{2})(LT)?\b",
        text,
    )

    if flotation_match:
        flotation_size = flotation_match.group(1)
        lt_suffix = flotation_match.group(2) or ""

        return f"{flotation_size}{lt_suffix}"

    return None


def standardize_existing_giga_priority(df):
    df = df.copy()
    df["website"] = (
        df["source"]
        .map(
            {
                "giga": "Giga",
                "priority": "Priority",
                "Giga": "Giga",
                "Priority": "Priority",
            }
        )
        .fillna(df["source"])
    )
    df["model"] = df["model"].apply(normalize_model)
    df["price_per_tire"] = df["price_per_tire"].apply(clean_money)
    df["in_stock"] = df["in_stock"].apply(normalize_bool)
    df["part_number"] = first_available(df, ["part_number", "sku"])
    df["url"] = first_available(df, ["url", "sourceUrl"])
    df = df[df["in_stock"]]
    return df[[
        "website", "model", "size", "price_per_tire", "in_stock",
        "part_number", "url",
    ]]


def standardize_simpletire(df):
    df = df.copy()
    df["website"] = "Simple Tire"
    df["model"] = df["productName"].apply(normalize_model)
    df["price_per_tire"] = df["price"].apply(clean_money)
    df["in_stock"] = df["price_per_tire"].notna()
    df["part_number"] = first_available(df, ["part_number", "mpn", "partNumber"])
    df["url"] = first_available(df, ["url", "sourceUrl"])
    df = df[df["in_stock"]]
    return df[[
        "website", "model", "size", "price_per_tire", "in_stock",
        "part_number", "url",
    ]]


def main():
    existing_files = sorted(glob.glob("tire_prices_*.csv"))
    if not existing_files:
        raise FileNotFoundError(
            "No tire_prices_*.csv file found from the combined scraper."
        )

    latest_existing_file = existing_files[-1]
    print(f"Reading Giga/Priority file: {latest_existing_file}")
    existing_df = pd.read_csv(latest_existing_file)
    all_frames = [standardize_existing_giga_priority(existing_df)]

    if os.path.exists("simpletire_raw.csv"):
        print("Reading SimpleTire file: simpletire_raw.csv")
        simple_df = pd.read_csv("simpletire_raw.csv")
        all_frames.append(standardize_simpletire(simple_df))
    else:
        print("No simpletire_raw.csv found. Keeping only Giga/Priority rows.")

    final_df = pd.concat(all_frames, ignore_index=True)
    final_df["RAW SIZE"] = final_df.apply(
        lambda row: raw_size_from_size(row["size"], row["website"]),
        axis=1,
    )
    final_df["DATE"] = TODAY

    map_df = pd.read_csv("scraper/map_prices.csv")
    map_df["model"] = map_df["Brand+Product"].apply(normalize_model)
    map_df["RAW SIZE"] = map_df["Rawsize"].apply(raw_size_from_size)
    map_df["MAP"] = map_df["MAP"].apply(clean_money)

    final_df = final_df.merge(
        map_df[["model", "RAW SIZE", "MAP"]],
        how="left",
        on=["model", "RAW SIZE"],
    )
    final_df = final_df[
        [
            "model",
            "size",
            "price_per_tire",
            "website",
            "part_number",
            "url",
            "RAW SIZE",
            "DATE",
            "MAP",
        ]
    ]

    output_file = f"tire_prices_{TODAY}.csv"
    final_df.to_csv(output_file, index=False)
    print(f"Saved standardized file: {output_file}")
    print(f"Rows after keeping only in-stock rows: {len(final_df)}")

    missing_map = final_df[final_df["MAP"].isna()]
    if not missing_map.empty:
        print(f"Warning: {len(missing_map)} rows did not match MAP.")
        print(
            missing_map[["website", "model", "size", "RAW SIZE"]]
            .head(20)
            .to_string(index=False)
        )


if __name__ == "__main__":
    main()
