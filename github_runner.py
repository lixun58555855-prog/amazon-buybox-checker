import os
import sys
import io
import json
import csv
import re
import time
import random
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright

# Force UTF-8 stdout
if sys.platform.startswith('win'):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# Marketplace Configuration with latest accurate postal codes
MARKETPLACES = {
    "US": {
        "name": "美国站",
        "domain": "https://www.amazon.com",
        "postal": "10041",
        "flag": "🇺🇸"
    },
    "CA": {
        "name": "加拿大站",
        "domain": "https://www.amazon.ca",
        "postal": "M5J 2N8",
        "flag": "🇨🇦"
    },
    "UK": {
        "name": "英国站",
        "domain": "https://www.amazon.co.uk",
        "postal": "E14 5AB",
        "flag": "🇬🇧"
    },
    "DE": {
        "name": "德国站",
        "domain": "https://www.amazon.de",
        "postal": "10115",
        "flag": "🇩🇪"
    }
}

def parse_item(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    parts = line.split(None, 1)
    target = parts[0].strip()
    expected_seller = parts[1].strip() if len(parts) > 1 else ""

    site = "CA"
    asin = None

    if "amazon.com" in target:
        site = "US"
    elif "amazon.co.uk" in target:
        site = "UK"
    elif "amazon.de" in target:
        site = "DE"
    elif "amazon.ca" in target:
        site = "CA"
    elif ":" in target:
        prefix_parts = target.split(":", 1)
        prefix = prefix_parts[0].strip().upper()
        if prefix in MARKETPLACES:
            site = prefix
            target = prefix_parts[1].strip()

    if re.fullmatch(r'[A-Za-z0-9]{10}', target):
        asin = target.upper()
    else:
        match = re.search(r'/(?:dp|gp/product|product)/([A-Za-z0-9]{10})', target)
        if match:
            asin = match.group(1).upper()
        else:
            for part in re.split(r'[/\\?&=#\s]+', target):
                if re.fullmatch(r'[A-Za-z0-9]{10}', part) and not part.lower().startswith('http'):
                    asin = part.upper()
                    break

    if asin:
        return {
            "site": site,
            "asin": asin,
            "expected_seller": expected_seller
        }
    return None

def is_robot_check(page):
    title = page.title().strip().lower()
    html = page.content().lower()
    if "validatecaptcha" in html or "robot check" in title or "captcha" in title:
        return True
    if "type the characters you see in this image" in html or "api-services-support@amazon.com" in html:
        return True
    if title == "amazon.com" and page.locator("span#productTitle, h1#title").count() == 0:
        return True
    return False

def try_solve_captcha(page):
    """
    智能破解亚马逊图片验证码：
    自动定位页面上的验证码图片，调用 amazoncaptcha 离线识别库毫秒级秒破并提交！
    """
    try:
        html = page.content().lower()
        if not ("validatecaptcha" in html or "type the characters" in html or "enter the characters" in html or "/captcha/" in html):
            return False

        print("  [🛡️ 智能解封] 正在探测验证码图片并尝试自动破解...")
        from amazoncaptcha import AmazonCaptcha

        img_elem = page.locator("form[action*='validateCaptcha'] img, img[src*='captcha']").first
        if img_elem.count() > 0:
            img_url = img_elem.get_attribute("src")
            if img_url:
                print(f"  [🛡️ 智能解封] 捕获验证码图片: {img_url[:60]}...")
                captcha = AmazonCaptcha.fromlink(img_url)
                solution = captcha.solve()
                print(f"  [🎉 智能解封] 验证码成功识别为: [{solution}]，正在自动填写提交...")

                input_elem = page.locator("#captchacharacters, input[name='field-keywords']").first
                if input_elem.count() > 0 and solution:
                    input_elem.fill(solution)
                    time.sleep(0.5)
                    submit_btn = page.locator("button[type='submit'], input[type='submit']").first
                    if submit_btn.count() > 0:
                        submit_btn.click()
                    else:
                        input_elem.press("Enter")
                    
                    page.wait_for_timeout(3500)
                    if not is_robot_check(page):
                        print("  [✅ 智能解封] 验证码验证通过！已成功直达真实商品详情页！")
                        return True
                    else:
                        print("  [⚠️ 智能解封] 本次识别结果未被接受，正在准备下一次尝试...")
    except Exception as e:
        print(f"  [⚠️ 智能解封] 自动破解过程出现提示: {e}")

    return False

def inspect_asin(page, site, asin, expected_seller=""):
    cfg = MARKETPLACES[site]
    url = f"{cfg['domain']}/dp/{asin}"
    result = {
        "site": site,
        "site_name": f"{cfg['flag']} {cfg['name']}",
        "asin": asin,
        "url": url,
        "expected_seller": expected_seller or "未指定",
        "has_buybox": False,
        "has_add_to_cart": False,
        "has_buy_now": False,
        "is_suppressed": False,
        "is_unavailable": False,
        "is_dog_page": False,
        "is_captcha": False,
        "title": "N/A",
        "price": "N/A",
        "seller": "N/A",
        "availability": "N/A",
        "delivery_location": "N/A",
        "status_label": "未知"
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)

            title = page.title()
            # 1. 检查是否为 404 变狗
            if "Page Not Found" in title or "Looking for something?" in title or "Hunde" in title:
                result["is_dog_page"] = True
                result["status_label"] = "❌ 404变狗"
                return result

            # 2. 检查是否触发亚马逊验证码/风控拦截
            if is_robot_check(page):
                solved = try_solve_captcha(page)
                if not solved:
                    if attempt < max_retries - 1:
                        wait_sec = random.uniform(3, 5)
                        print(f"  [⚠️ 风控提示] 遭遇亚马逊验证码拦截，等待 {wait_sec:.1f} 秒后重试第 {attempt+1} 次...")
                        time.sleep(wait_sec)
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                        if is_robot_check(page):
                            continue
                    else:
                        result["is_captcha"] = True
                        result["status_label"] = "🔄 亚马逊风控验证码"
                        result["title"] = "Amazon Robot Check"
                        return result

            # 3. 提取商品标题
            prod_title_elem = page.locator("span#productTitle, h1#title")
            if prod_title_elem.count() > 0:
                result["title"] = prod_title_elem.first.inner_text().strip()
            else:
                result["title"] = title

            # 4. 提取配送地址
            loc_elem = page.locator("#glow-ingress-block")
            if loc_elem.count() > 0:
                result["delivery_location"] = " ".join(loc_elem.first.inner_text().split()).replace('\u200c', '')

            # 5. 购物车/立即购买按钮检测
            html = page.content()
            has_add_to_cart = (
                ('id="add-to-cart-button"' in html) or 
                ('name="submit.add-to-cart"' in html) or
                ('id="freshAddToCartButton"' in html) or
                (page.locator("#add-to-cart-button, input[name='submit.add-to-cart']").count() > 0)
            )
            has_buy_now = (
                ('id="buy-now-button"' in html) or 
                ('name="submit.buy-now"' in html) or
                ('id="one-click-button"' in html) or
                (page.locator("#buy-now-button, input[name='submit.buy-now']").count() > 0)
            )
            
            result["has_add_to_cart"] = has_add_to_cart
            result["has_buy_now"] = has_buy_now

            # 6. 价格抓取
            price_elem = page.locator("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, #corePrice_desktop .a-price .a-offscreen, .a-price .a-offscreen")
            if price_elem.count() > 0:
                result["price"] = price_elem.first.inner_text().strip()

            # 7. 抓取当前赢得购物车的真实卖家
            current_seller = ""
            seller_elem = page.locator("#sellerProfileTriggerId, #merchant-info")
            if seller_elem.count() > 0:
                current_seller = " ".join(seller_elem.first.inner_text().split())
            else:
                tabular_seller = page.locator("#tabular-buybox .tabular-buybox-container, #tabular-buybox")
                if tabular_seller.count() > 0:
                    current_seller = " ".join(tabular_seller.first.inner_text().split()[:10])
                else:
                    if has_add_to_cart:
                        current_seller = "Amazon自营或FBA"
                    else:
                        current_seller = "无"
            result["seller"] = current_seller

            # 8. 库存状态
            avail_elem = page.locator("#availability")
            if avail_elem.count() > 0:
                avail_text = " ".join(avail_elem.first.inner_text().split())
                result["availability"] = avail_text
                if any(k in avail_text.lower() for k in ["currently unavailable", "derzeit nicht verfügbar", "temporarily out of stock"]):
                    result["is_unavailable"] = True

            has_see_all = (not has_add_to_cart) and (('id="buybox-see-all-buying-choices"' in html) or ('id="all-offers-display"' in html))
            result["is_suppressed"] = has_see_all

            # 9. 核心判定逻辑 (是否正常在售 / 丢失 / 被抢跟卖)
            if has_add_to_cart or has_buy_now:
                if expected_seller:
                    # 支持以 | 或逗号分隔多个允许的卖家 (例如: TMC CO., LTD|Amazon US)
                    allowed_sellers = [s.strip().lower() for s in re.split(r'[,|/]', expected_seller) if s.strip()]
                    cur_lower = current_seller.lower()
                    
                    matched = any(s in cur_lower for s in allowed_sellers)
                    
                    if matched:
                        result["has_buybox"] = True
                        result["status_label"] = "🟢 正常在售(赢得购物车)"
                    else:
                        result["has_buybox"] = False
                        result["status_label"] = f"🚨 被抢跟卖 (当前: {current_seller})"
                else:
                    result["has_buybox"] = True
                    result["status_label"] = "🟢 正常在售"
            elif result["is_unavailable"]:
                result["status_label"] = "⚪ 缺货"
            elif has_see_all:
                result["status_label"] = "🔴 购物车被抑制"
            else:
                result["status_label"] = "⚠️ 无购物车"

            return result

        except Exception as e:
            if attempt == max_retries - 1:
                result["status_label"] = f"检测异常 ({str(e)[:20]})"
            else:
                time.sleep(2)

    return result

def switch_postal_code(page, site):
    cfg = MARKETPLACES[site]
    domain = cfg["domain"]
    postal = cfg["postal"]
    print(f"[{site}] 正在访问 {domain} 并自动切换当地邮编为 [{postal}]...")
    
    for attempt in range(2):
        try:
            page.goto(domain, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)

            # 检查是否有验证码
            if is_robot_check(page):
                try_solve_captcha(page)

            # 接受 Cookie 弹窗 (欧洲站 GDPR 必须)
            cookie_btn = page.locator("#sp-cc-accept, input#sp-cc-accept")
            if cookie_btn.count() > 0:
                try:
                    cookie_btn.first.click()
                    page.wait_for_timeout(1000)
                except Exception:
                    pass

            # 执行 Glow 地址切换 API
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
            
            # 刷新页面让地址 Cookie 彻底生效
            page.wait_for_timeout(1000)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            loc_elem = page.locator("#glow-ingress-block")
            current_loc = " ".join(loc_elem.first.inner_text().split()).replace('\u200c', '') if loc_elem.count() > 0 else "未知"
            print(f"[{site}] 本地邮编 [{postal}] 切换完毕！当前配送地址栏显示: [{current_loc}]")
            return
        except Exception as e:
            print(f"[{site}] 切换邮编第 {attempt+1} 次尝试异常: {e}")
            page.wait_for_timeout(2000)

def send_wechat_alert(anomalies, total_count, captchas=None):
    if not anomalies:
        print("[微信提醒] 结果检测完毕：所有站点商品均正常在售（无被抢跟卖、无丢购物车），无需推送打扰。")
        return

    pushplus_token = os.environ.get("PUSHPLUS_TOKEN", "").strip()

    if not pushplus_token:
        print(f"\n[微信推送提示] ⚠️ 发现 {len(anomalies)} 个商品异常，但尚未配置 PUSHPLUS_TOKEN。")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"🚨【亚马逊跨站点】发现 {len(anomalies)} 个商品购物车异常/被抢！"
    lines = [
        f"### 🚨 亚马逊跨站点 · 购物车异常/被跟卖报警！",
        f"- **检测时间**: {now_str}",
        f"- **检测总数**: {total_count} 个",
        f"- **异常数量**: **{len(anomalies)} 个**",
        "",
        "| 站点 | ASIN | 状态 | 我的店铺 | 当前赢家 | 价格 | 直达链接 |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: |"
    ]
    for a in anomalies:
        lines.append(f"| {a['site_name']} | **`{a['asin']}`** | {a['status_label']} | {a['expected_seller']} | {a['seller']} | {a['price']} | [点击查看]({a['url']}) |")

    if captchas:
        lines.append("")
        lines.append(f"> ℹ️ 另有 {len(captchas)} 个商品遭遇亚马逊临时风控拦截（已自动跳过未发丢车报警，将在下次巡检重试）。")

    lines.append("")
    lines.append("> ⚠️ 提示：若显示【被抢跟卖】，说明其他卖家正在低价窃取流量，请尽快排查处理！")
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

def load_asins_from_disk():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in ["asins.txt", "asin.txt"]:
        paths_to_try = [
            fname,
            os.path.join(base_dir, fname)
        ]
        for p in paths_to_try:
            if os.path.exists(p):
                print(f"[读取配置] 成功找到监控文件: {p}")
                for enc in ["utf-8-sig", "utf-8", "gbk"]:
                    try:
                        with open(p, "r", encoding=enc) as f:
                            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
                            if lines:
                                print(f"[读取配置] 成功加载了 {len(lines)} 行商品数据")
                                return lines
                    except Exception:
                        continue
    return []

def main():
    raw_lines = load_asins_from_disk()

    if not raw_lines and len(sys.argv) > 1:
        raw_lines.extend(sys.argv[1:])

    items = []
    for line in raw_lines:
        parsed = parse_item(line)
        if parsed and parsed not in items:
            items.append(parsed)

    if not items:
        print("❌ 错误：未能从 asins.txt / asin.txt 中读取到任何有效的 ASIN！")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始多站点检测任务，共 {len(items)} 个目标...")

    grouped = {}
    for it in items:
        grouped.setdefault(it["site"], []).append(it)

    results = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True)

        for site, site_items in grouped.items():
            locale_map = {
                "US": "en-US",
                "CA": "en-CA",
                "UK": "en-GB",
                "DE": "de-DE"
            }
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale=locale_map.get(site, "en-US"),
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1"
                }
            )
            page = context.new_page()
            # 隐藏 Playwright 特征，防止触发亚马逊风控/验证码
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            switch_postal_code(page, site)

            for idx, item in enumerate(site_items, 1):
                asin = item["asin"]
                exp_seller = item["expected_seller"]
                print(f"[{site}] ({idx}/{len(site_items)}) 正在检查 {asin} (我的店铺: {exp_seller or '未指定'})...")
                res = inspect_asin(page, site, asin, expected_seller=exp_seller)
                print(f"  -> 结果: {res['status_label']} | 价格: {res['price']} | 赢家: {res['seller']}")
                results.append(res)
                
                # 随机平滑间隔 2.5~4.5 秒，模拟真实人类浏览，彻底防止亚马逊按频率拉黑封控
                sleep_sec = random.uniform(2.5, 4.5)
                time.sleep(sleep_sec)

            context.close()

        browser.close()

    # CSV report
    csv_file = "buybox_report.csv"
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["站点", "ASIN", "商品标题", "购物车状态", "当前价格", "我的店铺", "赢得购物车卖家", "配送邮编", "Add-to-cart是否存在", "商品链接"])
        for r in results:
            writer.writerow([r["site_name"], r["asin"], r["title"], r["status_label"], r["price"], r["expected_seller"], r["seller"], r["delivery_location"], r["has_add_to_cart"], r["url"]])
    print(f"\n报表已生成: {csv_file}")

    # 分离真实异常与风控拦截：风控拦截不作为购物车被抢进行骚扰报警
    anomalies = [r for r in results if (not r["has_buybox"]) and (not r.get("is_captcha"))]
    captchas = [r for r in results if r.get("is_captcha")]
    
    send_wechat_alert(anomalies, len(results), captchas=captchas)

    # GitHub Step Summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("# 🌍 亚马逊多站点 (美/加/英/德) 购物车与防跟卖检测报告\n\n")
            f.write(f"- **检测时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
            f.write(f"- **检测总数**: {len(results)} 个 | 🚨 **异常/被抢报警**: {len(anomalies)} 个")
            if captchas:
                f.write(f" | ℹ️ **风控拦截跳过**: {len(captchas)} 个\n\n")
            else:
                f.write("\n\n")
            f.write("| 站点 | ASIN | 状态 | 价格 | 我的店铺 | 赢得卖家 | 配送地址 | 链接 |\n")
            f.write("| :---: | :--- | :---: | :---: | :---: | :--- | :---: | :---: |\n")
            for r in results:
                f.write(f"| {r['site_name']} | **`{r['asin']}`** | {r['status_label']} | {r['price']} | {r['expected_seller']} | {r['seller']} | {r['delivery_location']} | [查看商品]({r['url']}) |\n")
        print("GitHub Step Summary 已更新！")

if __name__ == "__main__":
    main()
