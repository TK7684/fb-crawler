from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
b = pw.chromium.launch(headless=False)
page = b.new_page()
page.goto('https://www.facebook.com/login')
input('>>> Log in to Facebook, then press Enter here... <<<')
import json
cookies = b.contexts[0].cookies()
json.dump(cookies, open('session.json', 'w'), indent=2)
print(f'Saved {len(cookies)} cookies!')
b.close()
pw.stop()
