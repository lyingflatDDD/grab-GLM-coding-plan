#!/usr/bin/env python
# -*-coding:utf-8 -*-

"""
# File       : glm.py
# Description：bigmodel.cn/glm-coding 抢购 + 腾讯点选验证码自动识别
"""
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright
from src import captcha

cap = captcha.TextSelectCaptcha()
URL = "https://bigmodel.cn/glm-coding"

# 抢购配置
CONFIG = {
    "target_plan": "Pro",       # Lite / Pro / Max
    "billing_cycle": "quarter", # month / quarter / year
    "target_hour": 10,
    "target_minute": 0,
    "target_second": 0,
}

CYCLE_LABELS = {"month": "连续包月", "quarter": "连续包季", "year": "连续包年"}


def log_console_message(msg):
    text = msg.text
    if text:
        print(f'Console: {text}')


def init(page):
    page.on('console', log_console_message)

    # 隐藏 webdriver 特征
    page.add_init_script('''() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
    }''')

    # 拦截限流接口（与 JS 版一致）
    page.route('**/api/biz/rate-limit/check**', lambda route: route.fulfill(
        status=200,
        content_type='application/json',
        body='{"code":0,"msg":"success","data":null,"success":true}'
    ))

    # 拦截售罄数据
    def handle_response(response):
        if response.status != 200:
            return
        ct = response.headers.get('content-type', '')
        if 'application/json' not in ct:
            return
        try:
            body = response.text()
            if any(k in body for k in ['"isSoldOut":true', '"disabled":true', '"soldOut":true']):
                body \
                    .replace('"isSoldOut":true', '"isSoldOut":false') \
                    .replace('"disabled":true', '"disabled":false') \
                    .replace('"soldOut":true', '"soldOut":false') \
                    .replace('"stock":0', '"stock":999')
                print("[拦截] 售罄数据已篡改")
        except Exception:
            pass

    page.on('response', handle_response)


def handle_tencent_captcha(page):
    """
    处理腾讯点选验证码（tcaptcha）
    返回: True 处理成功，False 失败
    """
    try:
        # 腾讯验证码可能在 iframe 中
        captcha_frame = None
        try:
            iframe_element = page.wait_for_selector(
                'iframe[id*="tcaptcha"], iframe[src*="captcha"]', timeout=3000
            )
            if iframe_element:
                captcha_frame = iframe_element.content_frame()
        except Exception:
            pass

        # 在主页面或 iframe 中查找验证码图片
        target = captcha_frame or page

        # 等待验证码图片加载
        img_element = target.wait_for_selector(
            'img[src*="captcha"], img.tcaptcha-verify-img, .tcaptcha-bg-img img, '
            'img[onclick], .tcaptcha-main-img img',
            timeout=8000
        )
        if not img_element:
            print("未找到验证码图片元素")
            return False

        # 获取图片 URL
        img_url = img_element.get_attribute('src')
        if not img_url:
            print("未获取到验证码图片 URL")
            return False
        print(f"验证码图片 URL: {img_url[:80]}...")

        # 下载图片并识别
        resp = requests.get(img_url)
        content = resp.content
        plan = cap.run_dict(content)

        if not plan or not plan.get("point"):
            print("模型未识别到点击目标")
            return False

        orig_w, orig_h = plan.get("imgW"), plan.get("imgH")
        print(f"图片尺寸: {orig_w} x {orig_h}, 识别到 {len(plan['point'])} 个目标")

        # 获取验证码图片在页面上的显示位置和尺寸
        img_box = img_element.bounding_box()
        if not img_box:
            print("无法获取验证码图片位置")
            return False

        display_w, display_h = img_box['width'], img_box['height']
        print(f"显示尺寸: {display_w} x {display_h}")

        scale_x = display_w / orig_w
        scale_y = display_h / orig_h
        X, Y = img_box['x'], img_box['y']

        time.sleep(0.8)

        # 依次点击每个目标
        for i, point in enumerate(plan["point"]):
            x_rel = point.get("x_rel")
            y_rel = point.get("y_rel")
            click_x = X + x_rel * scale_x
            click_y = Y + y_rel * scale_y
            print(f"  点击第 {i+1} 个目标: ({click_x:.0f}, {click_y:.0f})")
            page.mouse.click(click_x, click_y)
            time.sleep(0.8)

        # 查找并点击确认按钮
        time.sleep(0.5)
        confirm_selectors = [
            'a.tcaptcha-verify-btn',
            'button.tcaptcha-verify-btn',
            '.tcaptcha-verify-btn',
            '#tcaptcha-verify-btn',
        ]
        for sel in confirm_selectors:
            try:
                btn = target.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    print("已点击确认按钮")
                    break
            except Exception:
                continue

        print("验证码点击完成，已提交")
        return True

    except Exception as e:
        print(f"验证码处理异常: {e}")
        return False


def wait_for_captcha_and_solve(page, timeout=30):
    """
    监测验证码出现并自动处理
    返回: True 验证码处理成功或无需验证，False 处理失败
    """
    print(f"监测验证码，最长等待 {timeout} 秒...")
    start = time.time()

    while time.time() - start < timeout:
        # 检查腾讯验证码是否出现
        captcha_visible = page.evaluate('''() => {
            // 检查主页面上的 tcaptcha
            const wrapper = document.getElementById('tcaptcha_transform_dy');
            if (wrapper) {
                const style = window.getComputedStyle(wrapper);
                if (style.position === 'fixed' &&
                    parseFloat(style.opacity) >= 0.5 &&
                    style.display !== 'none') {
                    return true;
                }
            }
            // 检查 iframe 中的验证码
            const iframes = document.querySelectorAll('iframe[src*="captcha"]');
            return iframes.length > 0;
        }''')

        if captcha_visible:
            print("检测到腾讯验证码，开始自动识别...")
            return handle_tencent_captcha(page)

        time.sleep(1)

    print("未检测到验证码")
    return True


def ensure_billing_cycle(page, cycle):
    """确保选中的计费周期正确"""
    label = CYCLE_LABELS.get(cycle)
    if not label:
        return False
    try:
        tabs = page.query_selector_all('.switch-tab-item')
        for tab in tabs:
            text = re.sub(r'\s+', '', tab.inner_text()).strip()
            if label in text:
                if 'active' in (tab.get_attribute('class') or ''):
                    return True
                tab.click()
                time.sleep(0.3)
                return True
    except Exception as e:
        print(f"切换计费周期异常: {e}")
    return False


def find_plan_card(page, plan_name):
    """查找指定套餐卡片"""
    try:
        cards = page.query_selector_all('.package-card-box .package-card')
        for card in cards:
            title = card.query_selector('.package-card-title .font-prompt')
            if title and title.inner_text().strip() == plan_name:
                return card
    except Exception:
        pass
    return None


def find_buy_button(card):
    """查找购买按钮"""
    if not card:
        return None
    try:
        btns = card.query_selector_all('button.buy-btn, .package-card-btn-box button')
        for btn in btns:
            if btn.is_visible():
                return btn
    except Exception:
        pass
    return None


def click_buy(page, plan_name="Pro", cycle="quarter"):
    """执行购买点击"""
    if not ensure_billing_cycle(page, cycle):
        print("计费周期切换失败")
        return False

    card = find_plan_card(page, plan_name)
    if not card:
        print(f"未找到 {plan_name} 套餐卡片")
        return False

    btn = find_buy_button(card)
    if not btn:
        print("未找到购买按钮")
        return False

    # 强制启用按钮（绕过禁用状态）
    page.evaluate('(btn) => { btn.disabled = false; btn.removeAttribute("disabled"); }', btn)
    btn.click()
    print(f"已点击 {plan_name} 购买按钮")
    return True


def detect_dialog(page):
    """检测弹窗状态，返回弹窗类型或 None"""
    return page.evaluate('''() => {
        const wrappers = document.querySelectorAll('.el-dialog__wrapper');
        for (const wrapper of wrappers) {
            if (wrapper.style.display === 'none') continue;
            const emptyWrap = wrapper.querySelector('.empty-data-wrap');
            if (emptyWrap && emptyWrap.textContent.includes('购买人数较多')) {
                return { type: 'busy' };
            }
            const payDialog = wrapper.querySelector('.pay-dialog') ||
                              wrapper.querySelector('.scan-code-box') ||
                              wrapper.querySelector('.confirm-pay-btn');
            if (payDialog) {
                const priceItems = wrapper.querySelectorAll('.price-item');
                for (const el of priceItems) {
                    const text = el.textContent.replace(/[￥\\\\s]/g, '').trim();
                    if (text.length > 0 && /\\\\d/.test(text)) {
                        return { type: 'success-pay' };
                    }
                }
                if (wrapper.querySelector('.confirm-pay-btn')) {
                    return { type: 'confirm-pay' };
                }
                return { type: 'empty-price' };
            }
        }
        return null;
    }''')


def main():
    target_time = time.strptime(
        f"{CONFIG['target_hour']}:{CONFIG['target_minute']}:{CONFIG['target_second']}",
        "%H:%M:%S"
    )
    plan = CONFIG["target_plan"]
    cycle = CONFIG["billing_cycle"]

    with sync_playwright() as p:
        # 使用本地 Chrome + 已登录的用户配置（需先关闭 Chrome）
        chrome_user_data = os.path.expanduser("~/.config/google-chrome")
        context = p.chromium.launch_persistent_context(
            user_data_dir=chrome_user_data,
            headless=False,
            channel="chrome",      # 使用系统安装的 Chrome
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        page.goto(URL, wait_until='domcontentloaded')
        init(page)

        print(f"页面已加载，目标套餐: {plan} ({CYCLE_LABELS[cycle]})")
        print(f"目标时间: {CONFIG['target_hour']:02d}:{CONFIG['target_minute']:02d}:{CONFIG['target_second']:02d}")

        retry_count = 0
        max_retry = 300
        completed = False

        while not completed and retry_count < max_retry:
            now = time.localtime()
            now_seconds = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
            target_seconds = target_time.tm_hour * 3600 + target_time.tm_min * 60 + target_time.tm_sec

            # 还没到时间，等待
            diff = target_seconds - now_seconds
            if diff > 60:
                print(f"\r倒计时: {diff // 60}分{diff % 60}秒", end='', flush=True)
                time.sleep(1)
                continue
            if diff > 0:
                print(f"\r倒计时: {diff}秒", end='', flush=True)
                time.sleep(0.1)
                continue

            print(f"\n已到目标时间，开始抢购...")

            # 处理弹窗
            dialog = detect_dialog(page)
            if dialog:
                dtype = dialog.get('type')
                if dtype in ('success-pay', 'confirm-pay'):
                    print("抢购成功！弹出支付窗口，请扫码支付")
                    completed = True
                    break
                elif dtype in ('busy', 'empty-price'):
                    retry_count += 1
                    print(f"[{retry_count}] 无效弹窗({dtype})，关闭重试...")
                    close_btn = page.query_selector('.el-dialog__wrapper:not([style*="display: none"]) .el-dialog__headerbtn')
                    if close_btn:
                        close_btn.click()
                    time.sleep(0.4)
                    continue

            # 检测验证码
            captcha_visible = page.evaluate('''() => {
                const w = document.getElementById('tcaptcha_transform_dy');
                if (!w) return false;
                const s = window.getComputedStyle(w);
                return s.position === 'fixed' && parseFloat(s.opacity) >= 0.5;
            }''')

            if captcha_visible:
                print("检测到验证码，自动识别中...")
                if not handle_tencent_captcha(page):
                    print("验证码识别失败，等待手动处理...")
                    time.sleep(5)
                time.sleep(1)
                continue

            # 点击购买
            if click_buy(page, plan, cycle):
                retry_count += 1
                print(f"[{retry_count}] 已点击购买，等待响应...")
                time.sleep(0.3)
            else:
                time.sleep(0.2)

        if completed:
            print("\n抢购流程完成！")
        elif retry_count >= max_retry:
            print(f"\n已达最大重试次数({max_retry})，停止")

        time.sleep(1000)


if __name__ == '__main__':
    main()
