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

# Postal code for Canada (Toronto by default)
DEFAULT_POSTAL = os.environ.get("AMAZON_POSTAL_CODE", "M5V 2T6")

def extract_asin(text):
    text = text.strip()
    if re.fullmatch(r'[A-Za-z0-9]{10}', text):
        return text.upper()
    match = re.search(r'/(?:dp|gp/product|product)/([A-Za-z0-9]{10})', text)
    if match:
        return match.group(1).upper()
    for part in re.split(r'[/\\?&=#\s]+', text):
        if re.fullmatch(r'[A-Za-z0-9]{10}', part) and not part.lower().startswith('http'):
            return part.upper()
    return None

def inspect_asin(page, asin):
    url = f"https://www.amazon.ca/dp/{asin}"
    result = {
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
        if "Page Not Found" in title or "Looking for something?" in title:
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
            result["availability"] = " ".join(avail_elem.first.inner_text().split())
            if "currently unavailable" in result["availability"].lower():
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

def send_wechat_alert(anomalies, total_count):
    if not anomalies:
        print("[微信提醒] 结果检测完毕：所有商品购物车均正常在售，无需推送打扰。")
        return

    pushplus_token = os.environ.get("PUSHPLUS_TOKEN", "").strip()

    if not pushplus_token:
        print(f"\n[微信推送提示] ⚠️ 发现 {len(anomalies)} 个商品购物车异常，但尚未配置 PUSHPLUS_TOKEN。")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"🚨【亚马逊加拿大】发现 {len(anomalies)} 个商品购物车异常！"

    lines = [
        f"### 🚨 亚马逊加拿大站 · 购物车异常报警！",
        f"- **检测时间**: {now_str}",
        f"- **检测总数**: {total_count} 个",
        f"- **异常数量**: **{len(anomalies)} 个**",
        "",
        "| ASIN | 异常状态 | 当前价格 | 赢得卖家 | 快捷链接 |",
        "| :--- | :---: | :---: | :--- | :---: |"
    ]
    for a in anomalies:
        lines.append(f"| **`{a['asin']}`** | {a['status_label']} | {a['price']} | {a['seller']} | [点击直达]({a['url']}) |")

    lines.append("")
    lines.append("> ⚠️ 提示：请尽快登录卖家后台排查是否被低价跟卖、价格失去竞争力或 Listing 审核受限。")
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
    asins = []
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            extracted = extract_asin(arg)
            if extracted and extracted not in asins:
                asins.append(extracted)
    
    if not asins and os.environ.get("INPUT_ASINS"):
        for item in re.split(r'[\r\n,;]+', os.environ["INPUT_ASINS"]):
            extracted = extract_asin(item)
            if extracted and extracted not in asins:
                asins.append(extracted)

    if not asins and os.path.exists("asins.txt"):
        with open("asins.txt", "r", encoding="utf-8") as f:
            for line in f:
                extracted = extract_asin(line)
                if extracted and extracted not in asins:
                    asins.append(extracted)

    if not asins:
        asins = ["B0B53CQYW1"]
        print("未指定 ASIN，使用默认示例: B0B53CQYW1")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始检测 {len(asins)} 个 ASIN (加拿大站点)...")
    print(f"目标邮编: {DEFAULT_POSTAL}")

    results = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-CA"
        )
        page = context.new_page()

        # Step 1: Open amazon.ca gateway and switch postal code
        print("[Step 1] 正在自动切换加拿大邮编...")
        try:
            page.goto("https://www.amazon.ca", wait_until="domcontentloaded", timeout=60000)
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
            }""", DEFAULT_POSTAL)
            print("[Step 1] 邮编切换成功！")
        except Exception as e:
            print(f"[Step 1] 警告: 邮编设置异常: {e}")

        # Step 2: Loop ASINs
        for idx, asin in enumerate(asins, 1):
            print(f"[Step 2] ({idx}/{len(asins)}) 正在检查 {asin}...")
            res = inspect_asin(page, asin)
            print(f"  -> 结果: {res['status_label']} | 价格: {res['price']} | 卖家: {res['seller']}")
            results.append(res)

        browser.close()

    # 3. Output CSV report
    csv_file = "buybox_report.csv"
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ASIN", "商品标题", "购物车状态", "当前价格", "赢得购物车卖家", "配送邮编", "Add-to-cart是否存在", "商品链接"])
        for r in results:
            writer.writerow([r["asin"], r["title"], r["status_label"], r["price"], r["seller"], r["delivery_location"], r["has_add_to_cart"], r["url"]])
    print(f"\n报表已生成: {csv_file}")

    # 4. Filter anomalies & Send WeChat Alert
    anomalies = [r for r in results if not r["has_buybox"]]
    send_wechat_alert(anomalies, len(results))

    # 5. Write to GitHub Step Summary (if running in GitHub Actions)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("# 🇨🇦 亚马逊加拿大站 ASIN 购物车检测报告\n\n")
            f.write(f"- **检测时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
            f.write(f"- **检测邮编**: `{DEFAULT_POSTAL}`\n")
            f.write(f"- **检测总数**: {len(results)} 个 | 🚨 **异常报警**: {len(anomalies)} 个\n\n")
            f.write("| ASIN | 状态 | 价格 | 赢得卖家 | 配送地址 | 链接 |\n")
            f.write("| :--- | :---: | :---: | :--- | :---: | :---: |\n")
            for r in results:
                f.write(f"| **`{r['asin']}`** | {r['status_label']} | {r['price']} | {r['seller']} | {r['delivery_location']} | [查看商品]({r['url']}) |\n")
        print("GitHub Step Summary 已更新！")

if __name__ == "__main__":
    main()
