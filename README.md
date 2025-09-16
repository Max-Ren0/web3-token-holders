# USDT Top Holders (ERC-20) — Last ~14 Days

Analyze top USDT holders on Ethereum by reconstructing balances from `Transfer` events within a recent time window.

## Features
- Auto window: `DAYS_BACK` → `START_BLOCK` (fallback to Etherscan if RPC blocked)
- Etherscan `tokentx` pagination with polite rate limiting
- Reconstruct balances and export Top-N CSV + charts

## How to Run
```bash
pip install -r requirements.txt
cp .env.example .env
# Fill ETHERSCAN_API_KEY and USDT contract, optionally set DAYS_BACK (default 14)
python token_holders.py
