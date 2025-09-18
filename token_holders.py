import os
import time
import requests
from collections import defaultdict
from dotenv import load_dotenv
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 无界面环境也能画图
import matplotlib.pyplot as plt
from web3 import Web3

# ---------------- Config ----------------
load_dotenv()
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "").strip()
RPC_URL = os.getenv("RPC_URL", "https://cloudflare-eth.com").strip()
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "").strip()

# 可二选一：留 START_BLOCK 空 + 用 DAYS_BACK 自动换算；或直接填 START_BLOCK 数字
START_BLOCK = os.getenv("START_BLOCK", "").strip()
END_BLOCK = os.getenv("END_BLOCK", "").strip()
TOP_N = int(os.getenv("TOP_N", "20"))
DAYS_BACK = int(os.getenv("DAYS_BACK", "14"))  # 新增：默认回溯 14 天

if not ETHERSCAN_API_KEY:
    raise SystemExit("Please set ETHERSCAN_API_KEY in .env")
if not Web3.is_address(CONTRACT_ADDRESS):
    raise SystemExit("Please set a valid ERC20 CONTRACT_ADDRESS in .env")

# ---------------- Helpers ----------------
def get_latest_block_via_rpc(rpc_url: str) -> int | None:
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
        # 某些公共 RPC 可能连接但不响应，这里显式调用一次
        return int(w3.eth.block_number)
    except Exception as e:
        print(f"[Warn] RPC latest block failed: {e}")
        return None

def get_latest_block_via_etherscan(api_key: str) -> int | None:
    try:
        url = "https://api.etherscan.io/api"
        params = {"module": "proxy", "action": "eth_blockNumber", "apikey": api_key}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        hexnum = data.get("result")
        if isinstance(hexnum, str) and hexnum.startswith("0x"):
            return int(hexnum, 16)
        return None
    except Exception as e:
        print(f"[Warn] Etherscan proxy latest block failed: {e}")
        return None

def estimate_start_block(latest_block: int, days_back: int) -> int:
    # 以太坊平均 ~12s/块 ≈ 7200 块/天
    blocks_per_day = 7200
    return max(0, latest_block - days_back * blocks_per_day)

# ---------------- Resolve start/end blocks ----------------
latest = None
if not START_BLOCK:
    # 先尝试 RPC，失败则用 Etherscan
    latest = get_latest_block_via_rpc(RPC_URL)
    if latest is None:
        latest = get_latest_block_via_etherscan(ETHERSCAN_API_KEY)
    if latest is None:
        raise SystemExit("Cannot determine latest block via RPC or Etherscan. Please set START_BLOCK manually.")
    START_BLOCK_INT = estimate_start_block(latest, DAYS_BACK)
    print(f"[Auto] latest={latest}, DAYS_BACK={DAYS_BACK}, use START_BLOCK={START_BLOCK_INT}")
else:
    START_BLOCK_INT = int(START_BLOCK)

END_BLOCK_INT = int(END_BLOCK) if END_BLOCK else None

# ---------------- ERC20 metadata ----------------
ERC20_MIN_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

# 这里的 RPC 仅用于读 symbol/decimals；如果失败，给个兜底
symbol, decimals = "TOKEN", 18
try:
    w3_meta = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 20}))
    token = w3_meta.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=ERC20_MIN_ABI)
    try:
        symbol = token.functions.symbol().call()
    except Exception:
        pass
    try:
        decimals = token.functions.decimals().call()
    except Exception:
        pass
except Exception as e:
    print(f"[Warn] RPC meta read failed (symbol/decimals): {e}")

print(f"Using token {symbol} (decimals={decimals}) at {CONTRACT_ADDRESS}")

# ---------------- Fetch transfers via Etherscan ----------------
BASE = "https://api.etherscan.io/api"
params_base = {
    "module": "account",
    "action": "tokentx",
    "contractaddress": CONTRACT_ADDRESS,
    "page": 1,
    "offset": 10000,   # etherscan 单页上限
    "sort": "asc",
    "apikey": ETHERSCAN_API_KEY,
    "startblock": START_BLOCK_INT,
}
if END_BLOCK_INT is not None:
    params_base["endblock"] = END_BLOCK_INT

def fetch_all_transfers():
    all_tx = []
    page = 1
    while True:
        params = dict(params_base)
        params["page"] = page
        r = requests.get(BASE, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "0" and data.get("message", "").lower().startswith("no transactions"):
            break
        result = data.get("result", [])
        if not result:
            break
        all_tx.extend(result)
        print(f"Fetched page {page} with {len(result)} tx; total={len(all_tx)}")
        if len(result) < params["offset"]:
            break
        page += 1
        time.sleep(0.21)  # 友好限速
    return all_tx

print("Fetching transfers from Etherscan ...")
txs = fetch_all_transfers()
if not txs:
    raise SystemExit("No transfers fetched. Try a different block range (DAYS_BACK) or token.")

# ---------------- Reconstruct balances ----------------
balances = defaultdict(int)
zero_addr = "0x0000000000000000000000000000000000000000"

for t in txs:
    from_addr = (t.get("from") or "").lower()
    to_addr = (t.get("to") or "").lower()
    value_raw = int(t.get("value", "0"))
    if from_addr and from_addr != zero_addr:
        balances[from_addr] -= value_raw
    if to_addr and to_addr != zero_addr:
        balances[to_addr] += value_raw

df = (
    pd.DataFrame(
        [{"address": addr, "balance": bal / (10 ** decimals)} for addr, bal in balances.items() if bal != 0]
    )
    .sort_values("balance", ascending=False)
    .reset_index(drop=True)
)

# ---------------- Outputs ----------------
out_csv = f"holders_{symbol}.csv"
df.to_csv(out_csv, index=False)
print(f"Wrote {out_csv} with {len(df)} holders (within scanned window).")

top = df.head(TOP_N).copy()
top_csv = f"top{TOP_N}_holders_{symbol}.csv"
top.to_csv(top_csv, index=False)
print(f"Wrote {top_csv}")

# Bar chart
plt.figure(figsize=(10, 6))
plt.bar(range(len(top)), top["balance"])
plt.xticks(range(len(top)), [a[:6] + "..." + a[-4:] for a in top["address"]], rotation=45, ha="right")
plt.title(f"Top {TOP_N} {symbol} Holders (by balance)")
plt.ylabel(f"Balance ({symbol})")
plt.tight_layout()
plt.savefig(f"top{TOP_N}_holders_{symbol}.png", dpi=200)

# Pie chart
plt.figure(figsize=(8, 8))
plt.pie(top["balance"], labels=[a[:6] + "..." + a[-4:] for a in top["address"]])
plt.title(f"Top {TOP_N} {symbol} Holders Share")
plt.tight_layout()
plt.savefig(f"top{TOP_N}_holders_share_{symbol}.png", dpi=200)

print("Done.")
