"""Local, read-only first MVP for XTB transaction review.

Run: python -m streamlit run dashboard.py
No files, accounts or transactions are uploaded to any third-party API by
this application. Run the Streamlit server only on your own computer.
"""

from __future__ import annotations

from collections import Counter
from io import BytesIO

import pandas as pd
import streamlit as st

from app.analytics.cash_ledger import daily_cash_changes
from app.domain.accounts import Currency, InvestmentAccount
from app.ingestion.normalizer import normalize_cash_operation
from app.ingestion.xtb_cash import read_cash_operations
from app.ingestion.xtb_open import read_open_positions


st.set_page_config(page_title="AI Investment Journal — MVP", layout="wide")
st.title("AI Investment Journal")
st.caption("MVP 4.1 • lokalny podgląd danych XTB • bez wyceny i rekomendacji inwestycyjnych")

st.info(
    "Wczytaj po jednym aktualnym eksporcie XLSX na rachunek. "
    "Wybierz prawdziwą walutę rozliczeniową danego rachunku — "
    "XTB nie podaje jej jednoznacznie w wykorzystywanym arkuszu."
)
uploads = st.file_uploader(
    "Eksporty XTB (.xlsx) — Cash Operations i Open Positions",
    type=["xlsx"],
    accept_multiple_files=True,
)
if not uploads:
    st.write("Po dodaniu eksportów zobaczysz operacje, przepływy gotówki i otwarte pozycje.")
    st.stop()

selected = []
for number, uploaded in enumerate(uploads, start=1):
    currency = st.selectbox(
        f"Waluta rachunku dla pliku {number}: {uploaded.name}",
        ["PLN", "EUR", "USD"],
        index=None,
        placeholder="Wybierz walutę rachunku",
        key=f"account_currency_{number}_{uploaded.name}",
    )
    selected.append((uploaded, currency))

if any(currency is None for _, currency in selected):
    st.warning("Wybierz waluty wszystkich rachunków, aby rozpocząć analizę.")
    st.stop()

accounts = []
used_ids = set()
for upload, currency_code in selected:
    try:
        content = upload.getvalue()
        operations = read_cash_operations(BytesIO(content))
        if not operations:
            raise ValueError("Brak operacji w arkuszu Cash Operations")
        ids = {op.account_id for op in operations}
        if len(ids) != 1:
            raise ValueError("Eksport zawiera niejednoznaczne ID rachunku")
        account_id = ids.pop()
        if account_id in used_ids:
            raise ValueError(
                "Ten rachunek występuje w więcej niż jednym eksporcie. "
                "Wybierz jeden plik na rachunek, aby uniknąć podwójnego liczenia."
            )
        used_ids.add(account_id)

        account = InvestmentAccount(account_id, Currency(currency_code))
        normalized = [normalize_cash_operation(op, account) for op in operations]
        days = daily_cash_changes(normalized)

        position_warning = None
        positions = None
        try:
            snapshot = read_open_positions(BytesIO(content))
            if snapshot.reported_account_id != account_id:
                # Some XTB exports contain inconsistent Open Positions headers.
                # Preserve the conservative corroboration rule from the CLI tool.
                if f"_{account_id}_" in upload.name:
                    position_warning = (
                        "Niespójne metadane ID w Open Positions; "
                        "potwierdzono ID z Cash Operations i nazwy pliku."
                    )
                    positions = snapshot.quantities
                else:
                    position_warning = (
                        "ID rachunku w Open Positions różni się od Cash Operations. "
                        "Nie pokazujemy pozycji z tego arkusza."
                    )
            else:
                positions = snapshot.quantities
        except (ValueError, KeyError, TypeError):
            position_warning = "Nie udało się bezpiecznie odczytać otwartych pozycji."

        accounts.append({
            "currency": currency_code,
            "operations": normalized,
            "days": days,
            "positions": positions,
            "position_warning": position_warning,
        })
    except Exception as exc:
        st.error(f"Nie można przetworzyć pliku {upload.name}: {type(exc).__name__}: {exc}")
        st.stop()

st.success(f"Odczytano {len(accounts)} rachunków; dane pozostają w pamięci tej sesji.")
selected_account = st.selectbox(
    "Wybierz rachunek do analizy",
    list(range(len(accounts))),
    format_func=lambda i: f"Rachunek {i + 1} ({accounts[i]['currency']})",
)
current = accounts[selected_account]
operations = current["operations"]
days = current["days"]
ccy = current["currency"]

cash_change = days[-1].change_since_first_operation
net_deposits = sum((row.deposit_change for row in days), start=days[0].cash_change * 0)
positions = current["positions"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Operacje XTB", f"{len(operations)}")
col2.metric("Zmiana gotówki w eksporcie", f"{cash_change:,.2f} {ccy}")
col3.metric("Wpłaty netto", f"{net_deposits:,.2f} {ccy}")
col4.metric("Otwarte instrumenty", "—" if positions is None else str(sum(q > 0 for q in positions.values())))

st.warning(
    "Zmiana gotówki jest sumą podpisanych kwot Cash Operations od pierwszego "
    "zdarzenia w eksporcie. NIE jest saldem rachunku ani wynikiem inwestycyjnym. "
    "Saldo początkowe nie zostało ustalone."
)

st.subheader("Skumulowana zmiana gotówki — daty UTC")
chart = pd.DataFrame({
    "data": [row.day for row in days],
    f"Zmiana gotówki ({ccy})": [float(row.change_since_first_operation) for row in days],
}).set_index("data")
st.line_chart(chart)
st.caption("Wykres ma punkty tylko w dniach z operacjami XTB; nie jest dzienną wyceną portfela.")

left, right = st.columns(2)
with left:
    st.subheader("Typy operacji")
    counts = Counter(op.operation_type.value for op in operations)
    st.dataframe(
        pd.DataFrame([{"Typ": name, "Liczba": count} for name, count in sorted(counts.items())]),
        hide_index=True,
        use_container_width=True,
    )
with right:
    st.subheader("Otwarte pozycje według XTB")
    if current["position_warning"]:
        st.warning(current["position_warning"])
    if positions is not None:
        frame = pd.DataFrame([
            {"Ticker XTB": ticker, "Liczba jednostek": str(quantity)}
            for ticker, quantity in sorted(positions.items()) if quantity > 0
        ])
        if frame.empty:
            st.write("Brak otwartych pozycji.")
        else:
            st.dataframe(frame, hide_index=True, use_container_width=True)
    else:
        st.write("Pozycje nie są dostępne dla tego eksportu.")

st.divider()
st.caption(
    "Brak agregacji między walutami. Kursy rynkowe i FX oraz P&L nie są jeszcze "
    "używane w tym widoku. Uruchamiaj aplikację tylko lokalnie; nie publikuj eksportów XTB."
)
