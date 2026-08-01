import os

script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'script.js')
with open(script_path, 'r', encoding='utf-8') as f:
    content = f.read()

helpers = """
// ⭐️ 글로벌 매핑 헬퍼 함수
function getMappedBroker(rawBroker) {
    if (!rawBroker) return '';
    const DEFAULT_BROKERS = {'264': '키움증권', '1': '키움증권', '238': '미래에셋증권', '2': '미래에셋증권', '247': 'NH투자증권', '3': 'NH투자증권', '243': '한국투자증권', '4': '한국투자증권', '240': '삼성증권', '5': '삼성증권', '271': '토스증권', '6': '토스증권', '218': 'KB증권', '278': '신한투자증권'};
    return DEFAULT_BROKERS[rawBroker] || rawBroker;
}

function getMappedSubAccount(rawSubAccount, accountName) {
    if (accountName) return accountName;
    if (!rawSubAccount) return '';
    if (typeof currentAccountMappings !== 'undefined' && currentAccountMappings.accounts) {
        const accInfo = currentAccountMappings.accounts[rawSubAccount.replace(/-/g, '')];
        if (accInfo && accInfo.alias) return accInfo.alias;
    }
    return rawSubAccount;
}
"""
if "function getMappedBroker" not in content:
    content = content.replace("let cloudEntries = [];", "let cloudEntries = [];\n" + helpers)

content = content.replace(
    "const brokers = [...new Set(cloudEntries.map(e => e.brokerAccount).filter(Boolean))].sort();",
    "const brokers = [...new Set(cloudEntries.map(e => getMappedBroker(e.brokerAccount)).filter(Boolean))].sort();"
)
content = content.replace(
    "const displayBroker = DEFAULT_BROKERS[broker] || broker;",
    "const displayBroker = broker;"
)

content = content.replace(
    "const subAccounts = [...new Set(cloudEntries.map(e => e.subAccount).filter(Boolean))].sort();",
    "const subAccounts = [...new Set(cloudEntries.map(e => getMappedSubAccount(e.subAccount, e.accountName)).filter(Boolean))].sort();"
)
content = content.replace(
    """            let displaySa = sa;
            if (typeof currentAccountMappings !== 'undefined' && currentAccountMappings.accounts) {
                const accInfo = currentAccountMappings.accounts[sa.replace(/-/g, '')];
                if (accInfo && accInfo.alias) displaySa = accInfo.alias;
            }
            html += `<option value="${sa.replace(/"/g, '&quot;')}">${displaySa}</option>`;""",
    """            html += `<option value="${sa.replace(/\"/g, '&quot;')}">${sa}</option>`;"""
)
content = content.replace(
    """                let displaySa = sa;
                if (typeof currentAccountMappings !== 'undefined' && currentAccountMappings.accounts) {
                    const accInfo = currentAccountMappings.accounts[sa.replace(/-/g, '')];
                    if (accInfo && accInfo.alias) displaySa = accInfo.alias;
                }
                subAccountHtml += `<option value="${sa.replace(/"/g, '&quot;')}">${displaySa}</option>`;""",
    """                subAccountHtml += `<option value="${sa.replace(/\"/g, '&quot;')}">${sa}</option>`;"""
)


content = content.replace(
    "(entry.brokerAccount || '') !== currentDashboardBroker",
    "getMappedBroker(entry.brokerAccount) !== currentDashboardBroker"
)
content = content.replace(
    "(entry.subAccount || '') !== currentDashboardSubAccount",
    "getMappedSubAccount(entry.subAccount, entry.accountName) !== currentDashboardSubAccount"
)
content = content.replace(
    "entry.brokerAccount === currentFilterBroker",
    "getMappedBroker(entry.brokerAccount) === currentFilterBroker"
)
content = content.replace(
    "entry.subAccount === currentFilterSubAccount",
    "getMappedSubAccount(entry.subAccount, entry.accountName) === currentFilterSubAccount"
)
content = content.replace(
    "(entry.brokerAccount || '') === currentFilterBroker",
    "getMappedBroker(entry.brokerAccount) === currentFilterBroker"
)
content = content.replace(
    "(entry.subAccount || '') === currentFilterSubAccount",
    "getMappedSubAccount(entry.subAccount, entry.accountName) === currentFilterSubAccount"
)
content = content.replace(
    "(entry.brokerAccount || '') !== currentChartBroker",
    "getMappedBroker(entry.brokerAccount) !== currentChartBroker"
)
content = content.replace(
    "(entry.subAccount || '') !== currentChartSubAccount",
    "getMappedSubAccount(entry.subAccount, entry.accountName) !== currentChartSubAccount"
)

with open(script_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done!")
