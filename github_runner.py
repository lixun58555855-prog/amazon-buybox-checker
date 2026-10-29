import os
import sys
import io
import json
import csv
import re
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright

# Force UTF-8 stdout
if sys.platform.startswith('win'):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# Marketplace Configuration
MARKETPLACES = {
    "US": {
        "name": "美国站",
        "domain": "https://www.amazon.com",
        "postal": "10001", # 纽约
        "flag": "🇺🇸"
    },
    "CA": {
        "name": "加拿大站",
        "domain": "https://www.amazon.ca",
        "postal": "M5V 2T6", # 多伦多
        "flag": "🇨🇦"
    },
    "UK": {
        "name": "英国站",
        "domain": "https://www.amazon.co.uk",
        "postal": "SW1A 1AA", # 伦敦
        "flag": "🇬🇧"
    },
    "DE": {
        "name": "德国站",
        "domain": "https://www.amazon.de",
        "postal": "10115", # 柏林
        "flag": "🇩🇪"
    }
}

def parse_item(line):
    """
    Parse input line to determine marketplace and ASIN.
    Supports:
    - Full URLs: https://www.amazon.com/dp/B0...
                 https://www.amazon.ca/dp/B0...
                 https://www.amazon.co.uk/dp/B0...
                 https://www.amazon.de/dp/B0...
    - Prefix: US:B0..., UK:B0..., DE:B0..., CA:B0...
    - Bare ASIN: defaults to CA (or US)
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    site = "CA" # Default
    asin = None

    # Check URL
    if "amazon.com" in line:
        site = "US"
    elif "amazon.co.uk" in line:
        site = "UK"
    elif "amazon.de" in line:
        site = "DE"
    elif "amazon.ca" in line:
        site = "CA"
    elif ":" in line:
        parts = line.split(":", 1)
        prefix = parts[0].strip().upper()
        if prefix in MARKETPLACES:
            site = prefix
            line = parts[1].strip()

    # Extract 10-char ASIN
    if re.fullmatch(r'[A-Za-z0-9]{10}', line):
        asin = line.upper()
    else:
        match = re.search(r'/(?:dp|gp/product|product)/([A-Za-z0-9]{10})', line)
        if match:
            asin = match.group(1).upper()
        else:
            for part in re.split(r'[/\\?&=#\s]+', line):
                if re.fullmatch(r'[A-Za-z0-9]{10}', part) and not part.lower().startswith('http'):
                    asin = part.upper()
                    break

    if asin:
        return {"site": site, "asin": asin}
    return None

def inspect_asin(page, site, asin):
    cfg = MARKETPLACES[site]
    url = f"{cfg['domain']}/dp/{asin}"
    result = {
        "site": site,
        "site_name": f"{cfg['flag']} {cfg['name']}",
        "asin": asin,
        "url": url,
        "has_buybox": False,
        "has_add_to_cart": False,
        "has_buy_now": False,
        "is_suppressed": False,
        "is_unavailable": False,
        "is_dog_page": False,
        "title": "N/A",
        "price": "N/A",
        "seller": "N/A",
        "availability": "N/A",
        "delivery_location": "N/A",
        "status_label": "未知"
    }

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        title = page.title()
        if "Page Not Found" in title or "Looking for something?" in title or "Hunde" in title:
            result["is_dog_page"] = True
            result["status_label"] = "❌ 404变狗"
            return result

        prod_title_elem = page.locator("span#productTitle, h1#title")
        if prod_title_elem.count() > 0:
            result["title"] = prod_title_elem.first.inner_text().strip()
        else:
            result["title"] = title

        loc_elem = page.locator("#glow-ingress-block")
        if loc_elem.count() > 0:
            result["delivery_location"] = " ".join(loc_elem.first.inner_text().split()).replace('\u200c', '')

        html = page.content()
        has_add_to_cart = ('id="add-to-cart-button"' in html) or ('name="submit.add-to-cart"' in html)
        has_buy_now = ('id="buy-now-button"' in html) or ('name="submit.buy-now"' in html)
        
        result["has_add_to_cart"] = has_add_to_cart
        result["has_buy_now"] = has_buy_now

        price_elem = page.locator("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, #corePrice_desktop .a-price .a-offscreen, .a-price .a-offscreen")
        if price_elem.count() > 0:
            result["price"] = price_elem.first.inner_text().strip()

        seller_elem = page.locator("#sellerProfileTriggerId, #merchant-info")
        if seller_elem.count() > 0:
            result["seller"] = " ".join(seller_elem.first.inner_text().split())
        else:
            tabular_seller = page.locator("#tabular-buybox .tabular-buybox-container")
            if tabular_seller.count() > 0:
                result["seller"] = " ".join(tabular_seller.first.inner_text().split()[:10])
            else:
                result["seller"] = "Amazon自营或无法解析"

        avail_elem = page.locator("#availability")
        if avail_elem.count() > 0:
            avail_text = " ".join(avail_elem.first.inner_text().split())
            result["availability"] = avail_text
            # Multilingual unavailable detection
            if any(k in avail_text.lower() for k in ["currently unavailable", "derzeit nicht verfügbar"]):
                result["is_unavailable"] = True

        has_see_all = (not has_add_to_cart) and (('id="buybox-see-all-buying-choices"' in html) or ('id="all-offers-display"' in html))
        result["is_suppressed"] = has_see_all

        if has_add_to_cart:
            result["has_buybox"] = True
            result["status_label"] = "🟢 正常在售"
        elif result["is_unavailable"]:
            result["status_label"] = "⚪ 缺货"
        elif has_see_all:
            result["status_label"] = "🔴 购物车被抑制"
        else:
            result["status_label"] = "⚠️ 无购物车"

    except Exception as e:
        result["status_label"] = f"检测异常 ({str(e)[:20]})"

    return result

def switch_postal_code(page, site):
    cfg = MARKETPLACES[site]
    domain = cfg["domain"]
    postal = cfg["postal"]
    print(f"[{site}] 正在访问 {domain} 并自动切换邮编为 [{postal}]...")
    try:
        page.goto(domain, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        page.evaluate("""async (zip) => {
            let csrf = '';
            const tokenInput = document.querySelector('input[name="glowValidationToken"]');
            if (tokenInput) csrf = tokenInput.value;
            const params = new URLSearchParams();
            params.append('locationType', 'LOCATION_INPUT');
            params.append('zipCode', zip);
            params.append('storeContext', 'generic');
            params.append('deviceType', 'web');
            params.append('pageType', 'Gateway');
            params.append('actionSource', 'glow');
            await fetch('/portal-migration/hz/glow/address-change?actionSource=glow', {
                method: 'POST',
                headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8', 'anti-csrftoken-a2z': csrf },
                body: params.toString()
            });
        }""", postal)
        print(f"[{site}] 本地邮编 [{postal}] 切换成功！")
    except Exception as e:
        print(f"[{site}] 警告: 邮编切换异常: {e}")

def send_wechat_alert(anomalies, total_count):
    if not anomalies:
        print("[微信提醒] 结果检测完毕：所有站点商品购物车均正常在售，无需推送打扰。")
        return

    pushplus_token = os.environ.get("PUSHPLUS_TOKEN", "").strip()

    if not pushplus_token:
        print(f"\n[微信推送提示] ⚠️ 发现 {len(anomalies)} 个商品购物车异常，但尚未配置 PUSHPLUS_TOKEN。")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"🚨【亚马逊多站点】发现 {len(anomalies)} 个商品购物车异常！"

    lines = [
        f"### 🚨 亚马逊跨站点 · 购物车异常报警！",
        f"- **检测时间**: {now_str}",
        f"- **检测总数**: {total_count} 个",
        f"- **异常数量**: **{len(anomalies)} 个**",
        "",
        "| 站点 | ASIN | 异常状态 | 价格 | 赢得卖家 | 直达链接 |",
        "| :---: | :--- | :---: | :---: | :--- | :---: |"
    ]
    for a in anomalies:
        lines.append(f"| {a['site_name']} | **`{a['asin']}`** | {a['status_label']} | {a['price']} | {a['seller']} | [点击查看]({a['url']}) |")

    lines.append("")
    lines.append("> ⚠️ 提示：请尽快排查是否被低价跟卖、价格失去竞争力或 Listing 审核受限。")
    content = "\n".join(lines)

    try:
        req = urllib.request.Request(
            "http://www.pushplus.plus/send",
            data=json.dumps({
                "token": pushplus_token,
                "title": title,
                "content": content,
                "template": "markdown"
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        res_data = json.loads(resp.read().decode("utf-8"))
        print(f"[微信推送-PushPlus] 响应状态: {res_data.get('code')} - {res_data.get('msg')}")
    except Exception as e:
        print(f"[微信推送-PushPlus] 发送异常: {e}")

def main():
    # 1. Collect items
    raw_lines = []
    if len(sys.argv) > 1:
        raw_lines.extend(sys.argv[1:])
    
    if os.environ.get("INPUT_ASINS"):
        raw_lines.extend(re.split(r'[\r\n,;]+', os.environ["INPUT_ASINS"]))

    if not raw_lines and os.path.exists("asins.txt"):
        with open("asins.txt", "r", encoding="utf-8") as f:
            raw_lines.extend(f.readlines())

    items = []
    for line in raw_lines:
        parsed = parse_item(line)
        if parsed and parsed not in items:
            items.append(parsed)

    if not items:
        # Default sample if empty
        items = [{"site": "CA", "asin": "B0B53CQYW1"}]
        print("未指定 ASIN，使用默认加拿大示例: B0B53CQYW1")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始多站点检测任务，共 {len(items)} 个目标...")

    # Group by site to switch postal code only once per site
    grouped = {}
    for it in items:
        grouped.setdefault(it["site"], []).append(it["asin"])

    results = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True)

        for site, asin_list in grouped.items():
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Switch postal code for this site
            switch_postal_code(page, site)

            # Loop through ASINs of this site
            for idx, asin in enumerate(asin_list, 1):
                print(f"[{site}] ({idx}/{len(asin_list)}) 正在检查 {asin}...")
                res = inspect_asin(page, site, asin)
                print(f"  -> 结果: {res['status_label']} | 价格: {res['price']} | 卖家: {res['seller']}")
                results.append(res)

            context.close()

        browser.close()

    # 3. Output CSV report
    csv_file = "buybox_report.csv"
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["站点", "ASIN", "商品标题", "购物车状态", "当前价格", "赢得购物车卖家", "配送邮编", "Add-to-cart是否存在", "商品链接"])
        for r in results:
            writer.writerow([r["site_name"], r["asin"], r["title"], r["status_label"], r["price"], r["seller"], r["delivery_location"], r["has_add_to_cart"], r["url"]])
    print(f"\n报表已生成: {csv_file}")

    # 4. Filter anomalies & Send WeChat Alert
    anomalies = [r for r in results if not r["has_buybox"]]
    send_wechat_alert(anomalies, len(results))

    # 5. Write to GitHub Step Summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("# 🌍 亚马逊多站点 (美/加/英/德) 购物车检测报告\n\n")
            f.write(f"- **检测时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
            f.write(f"- **检测总数**: {len(results)} 个 | 🚨 **异常报警**: {len(anomalies)} 个\n\n")
            f.write("| 站点 | ASIN | 状态 | 价格 | 赢得卖家 | 配送地址 | 链接 |\n")
            f.write("| :---: | :--- | :---: | :---: | :--- | :---: | :---: |\n")
            for r in results:
                f.write(f"| {r['site_name']} | **`{r['asin']}`** | {r['status_label']} | {r['price']} | {r['seller']} | {r['delivery_location']} | [查看商品]({r['url']}) |\n")
        print("GitHub Step Summary 已更新！")

if __name__ == "__main__":
    main()
