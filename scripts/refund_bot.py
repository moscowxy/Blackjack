#!/usr/bin/env python3
import os
import time
import json
import datetime
import requests
from typing import Dict, Set

CONTRACT_ADDRESS = "0x73612914c81A9c072333Ea9eA71A9B26A5B9A707".lower()
API_BASE = "https://api.etherscan.io/v2/api"
SLEEP_SECONDS = 3

TOKEN_PRICE_USD = 0.0473
BONUS_PER_USD = 2.81

REFUNDED_TOPIC0 = "0x63d78733e0f2061cf8d395bf28a94628476196c067bf55f6e139f290d9904aaa"
USDC_ADDRESS = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".lower()
USDT_ADDRESS = "0xdac17f958d2ee523a2206206994597c13d831ec7".lower()

SUBS_FILE = "subs.json"


def load_subscribers() -> Set[int]:
    if not os.path.exists(SUBS_FILE):
        return set()
    with open(SUBS_FILE, "r") as f:
        return set(int(x) for x in json.load(f))


def save_subscribers(subs: Set[int]):
    with open(SUBS_FILE, "w") as f:
        json.dump(list(subs), f)


def fetch_transactions(api_key: str, start_block: int, end_block: int, offset: int = 1000):
    params = {
        "chainid": 1,
        "module": "account",
        "action": "txlist",
        "address": CONTRACT_ADDRESS,
        "startblock": start_block,
        "endblock": end_block,
        "page": 1,
        "offset": offset,
        "sort": "asc",
        "apikey": api_key,
    }
    r = requests.get(API_BASE, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("result", []) if data.get("status") == "1" else []


def fetch_logs_for_tx(api_key: str, tx_hash: str, block_number: int):
    params = {
        "chainid": 1,
        "module": "logs",
        "action": "getLogs",
        "address": CONTRACT_ADDRESS,
        "fromBlock": block_number,
        "toBlock": block_number,
        "page": 1,
        "offset": 1000,
        "apikey": api_key,
    }
    r = requests.get(API_BASE, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "1":
        return []
    return [
        l
        for l in data.get("result", [])
        if l.get("transactionHash", "").lower() == tx_hash.lower()
    ]


def parse_uint256(data_hex: str, idx: int):
    raw = data_hex[2:]
    return int(raw[idx * 64 : (idx + 1) * 64], 16)


def parse_address(data_hex: str, idx: int):
    raw = data_hex[2:]
    return "0x" + raw[idx * 64 + 24 : (idx + 1) * 64]


def extract_token_amounts(logs):
    token_amounts: Dict[str, int] = {}
    for log in logs:
        topics = log.get("topics") or []
        if not topics:
            continue
        if topics[0].lower() != REFUNDED_TOPIC0.lower():
            continue

        data_hex = log.get("data", "")
        t0 = parse_address(data_hex, 1)
        a0 = parse_uint256(data_hex, 2)
        t1 = parse_address(data_hex, 3)
        a1 = parse_uint256(data_hex, 4)

        if t0 and a0 is not None:
            token_amounts[t0.lower()] = token_amounts.get(t0.lower(), 0) + a0
        if t1 and a1 is not None:
            token_amounts[t1.lower()] = token_amounts.get(t1.lower(), 0) + a1

    return token_amounts


def compute_refund_usd(token_amounts):
    usd = 0.0
    for addr, raw in token_amounts.items():
        if addr in (USDC_ADDRESS, USDT_ADDRESS):
            usd += raw / 1_000_000
    return usd


def send_telegram(token: str, chat_id: int, text: str):
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=10,
    )


def main():
    api = os.getenv("ETHERSCAN_API_KEY")
    tele = os.getenv("TELEGRAM_TOKEN")
    if not api or not tele:
        print("ENV eksik")
        return

    subs = load_subscribers()
    last_block = 0
    cumulative_usd = 0.0
    cumulative_bonus = 0.0
    last_info = None
    wallets = set()

    while True:
        txs = fetch_transactions(api, last_block + 1, 9999999999)
        for tx in txs:
            block = int(tx.get("blockNumber", "0"))
            last_block = max(last_block, block)

            logs = fetch_logs_for_tx(api, tx["hash"], block)
            amounts = extract_token_amounts(logs)
            if not amounts:
                continue

            refund = compute_refund_usd(amounts)
            cumulative_usd += refund
            bonus = refund * BONUS_PER_USD
            cumulative_bonus += bonus
            wallets.add(tx.get("from", "").lower())

            base_token = refund / TOKEN_PRICE_USD if refund > 0 else 0.0
            total_token = (cumulative_usd / TOKEN_PRICE_USD) + cumulative_bonus

            ts = datetime.datetime.utcfromtimestamp(int(tx.get("timeStamp"))).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

            msg = (
                "*Gensyn Refund*\n"
                f"Wallet: `{tx.get('from')}`\n"
                f"Refund: {refund:,.2f} $\n"
                f"Refunded Token: {base_token:,.2f} $AI + {bonus:,.2f} Bonus $AI\n\n"
                f"Total Refund: {cumulative_usd:,.2f} $\n"
                f"Total Refund Token: {total_token:,.2f} $AI\n"
                f"Time: {ts}"
            )

            for cid in subs:
                send_telegram(tele, cid, msg)

        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
