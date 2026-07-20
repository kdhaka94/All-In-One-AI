"""Seed a local Fineract with realistic banking data via the REST API.

Run after the stack is healthy:  python fineract/seed_fineract.py
Wipe with `docker compose -f fineract/docker-compose.yml down -v` to start clean.
"""
from __future__ import annotations

import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://localhost:8443/fineract-provider/api/v1"
AUTH = ("mifos", "password")
HEADERS = {"Fineract-Platform-TenantId": "default", "Content-Type": "application/json"}
DATE_FMT = "dd MMMM yyyy"
LOCALE = "en"
OPENING_DATE = "01 January 2020"
TXN_DATE = "01 March 2024"


def call(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    response = requests.request(
        method, f"{BASE}{path}", json=payload, params=params,
        auth=AUTH, headers=HEADERS, verify=False, timeout=60,
    )
    if response.status_code >= 300:
        print(f"ERROR {method} {path} -> {response.status_code}\n{response.text}", file=sys.stderr)
        response.raise_for_status()
    return response.json() if response.content else {}


def get_template_ids() -> dict:
    """Discover valid codes from Fineract templates (version-robust)."""
    loan_tmpl = call("GET", "/loanproducts/template", params={"tenantIdentifier": "default"})
    currency = "USD"
    strategy = "mifos-standard-strategy"
    strategies = loan_tmpl.get("transactionProcessingStrategyOptions", [])
    if strategies:
        strategy = strategies[0].get("code", strategy)
    return {"currency": currency, "strategy": strategy}


def main() -> None:
    ids = get_template_ids()
    currency, strategy = ids["currency"], ids["strategy"]

    payment_type_id = call("POST", "/paymenttypes", {
        "name": "Cash", "description": "Cash payments", "isCashPayment": True, "position": 1,
    })["resourceId"]

    office_id = call("POST", "/offices", {
        "name": "Downtown Branch", "parentId": 1, "openingDate": OPENING_DATE,
        "dateFormat": DATE_FMT, "locale": LOCALE,
    })["officeId"]

    staff_id = call("POST", "/staff", {
        "officeId": office_id, "firstname": "Nadia", "lastname": "Officer",
        "isLoanOfficer": True, "joiningDate": OPENING_DATE,
        "dateFormat": DATE_FMT, "locale": LOCALE,
    })["resourceId"]

    loan_product_id = call("POST", "/loanproducts", {
        "name": "Standard Personal Loan", "shortName": "SPL1", "currencyCode": currency,
        "digitsAfterDecimal": 2, "inMultiplesOf": 1, "principal": 5000,
        "numberOfRepayments": 12, "repaymentEvery": 1, "repaymentFrequencyType": 2,
        "interestRatePerPeriod": 2, "interestRateFrequencyType": 2, "amortizationType": 1,
        "interestType": 0, "interestCalculationPeriodType": 1,
        "transactionProcessingStrategyCode": strategy, "accountingRule": 1,
        "daysInYearType": 365, "daysInMonthType": 30,
        "isInterestRecalculationEnabled": False,
        "dateFormat": DATE_FMT, "locale": LOCALE,
    })["resourceId"]

    savings_product_id = call("POST", "/savingsproducts", {
        "name": "Regular Savings", "shortName": "RS1", "description": "Basic savings",
        "currencyCode": currency, "digitsAfterDecimal": 2, "inMultiplesOf": 1,
        "nominalAnnualInterestRate": 5, "interestCompoundingPeriodType": 1,
        "interestPostingPeriodType": 4, "interestCalculationType": 1,
        "interestCalculationDaysInYearType": 365, "accountingRule": 1,
        "locale": LOCALE,
    })["resourceId"]

    clients = [
        ("Petra", "Yton"), ("Marco", "Reyes"), ("Amina", "Khan"),
        ("Diego", "Silva"), ("Fatima", "Noor"),
    ]
    for first, last in clients:
        client_id = call("POST", "/clients", {
            "officeId": office_id, "staffId": staff_id, "firstname": first, "lastname": last,
            "legalFormId": 1, "active": True, "activationDate": OPENING_DATE,
            "dateFormat": DATE_FMT, "locale": LOCALE,
        })["clientId"]

        loan_id = call("POST", "/loans", {
            "clientId": client_id, "productId": loan_product_id, "loanType": "individual",
            "principal": 5000, "loanTermFrequency": 12, "loanTermFrequencyType": 2,
            "numberOfRepayments": 12, "repaymentEvery": 1, "repaymentFrequencyType": 2,
            "interestRatePerPeriod": 2, "amortizationType": 1, "interestType": 0,
            "interestCalculationPeriodType": 1, "transactionProcessingStrategyCode": strategy,
            "expectedDisbursementDate": OPENING_DATE, "submittedOnDate": OPENING_DATE,
            "loanOfficerId": staff_id, "dateFormat": DATE_FMT, "locale": LOCALE,
        })["loanId"]
        call("POST", f"/loans/{loan_id}", {"approvedOnDate": OPENING_DATE,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "approve"})
        call("POST", f"/loans/{loan_id}", {"actualDisbursementDate": OPENING_DATE,
             "transactionAmount": 5000, "dateFormat": DATE_FMT, "locale": LOCALE},
             params={"command": "disburse"})
        call("POST", f"/loans/{loan_id}/transactions", {"transactionDate": TXN_DATE,
             "transactionAmount": 450, "dateFormat": DATE_FMT, "locale": LOCALE},
             params={"command": "repayment"})

        savings_id = call("POST", "/savingsaccounts", {
            "clientId": client_id, "productId": savings_product_id,
            "submittedOnDate": OPENING_DATE, "dateFormat": DATE_FMT, "locale": LOCALE,
        })["savingsId"]
        call("POST", f"/savingsaccounts/{savings_id}", {"approvedOnDate": OPENING_DATE,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "approve"})
        call("POST", f"/savingsaccounts/{savings_id}", {"activatedOnDate": OPENING_DATE,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "activate"})
        call("POST", f"/savingsaccounts/{savings_id}/transactions", {"transactionDate": TXN_DATE,
             "transactionAmount": 1200, "paymentTypeId": payment_type_id,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "deposit"})

        print(f"seeded client {first} {last}: loan {loan_id}, savings {savings_id}")

    print("Seeding complete.")


if __name__ == "__main__":
    main()
