# Cloudflare Gemini API Proxy

This folder contains an optional Cloudflare Worker that relays requests from Mochi to Google's Gemini API. It is useful when you want Mochi's Gemini API traffic to use a URL you control. The Worker is defined in [`worker.js`](./worker.js).

## English

### What the Worker does

The Worker forwards incoming requests to `generativelanguage.googleapis.com`, keeping the request path, query string, method, body, and headers. It removes the `cf-connecting-ip` and `x-real-ip` headers before forwarding. It also relays successful WebSocket upgrades in both directions, which is needed for Gemini Live audio sessions. For ordinary HTTP responses, it adds permissive CORS response headers so a browser-based Mochi client can read the response. If the upstream request throws an exception, it returns a JSON response with status `502`.

The Worker does not provide an API key, change the selected model, or store chat data. Mochi still sends the API key configured by the user. Requests are sent to Google's Gemini API; this Worker is not an alternate AI provider.

### Deploy the Worker

1. Sign in to the [Cloudflare dashboard](https://dash.cloudflare.com/).
2. Open **Workers & Pages** and choose the option to create a Worker. Dashboard wording can change; a basic “Hello World” starter is enough.
3. Give the Worker a name and create it.
4. Open the Worker editor and replace the starter code with the complete contents of [`worker.js`](./worker.js). Copy the whole file, including the WebSocket relay helper at the top.
5. Save and deploy the Worker.
6. Open the deployed Worker and copy its public URL. It is usually a `workers.dev` address unless you have configured a custom domain. Use the URL Cloudflare shows for your Worker, not a link to the editor or to this source file.

### Connect Mochi to the Worker

1. Open Mochi's **Settings**.
2. Keep your Gemini API key configured. The Worker does not replace or supply that key.
3. Enable **Use a custom API base URL**.
4. Paste the deployed Worker URL as the base URL and save the settings. Do not append a model name or Gemini API endpoint path; Mochi and the Gemini SDK build the API paths, and the Worker preserves the incoming path and query string.
5. If you turn the custom URL option off, Mochi connects directly to Google's standard Gemini API endpoint again.

### Security, privacy, and limitations

- Anyone who can reach the deployed Worker URL can send requests through it. This Worker has no authentication, user allowlist, or rate limit of its own. Treat the URL as public and monitor your Cloudflare usage; unsolicited traffic can consume Worker resources or account limits.
- The Worker does not contain your Gemini API key. The key is supplied by Mochi with each API request. Someone who only discovers the Worker URL does not thereby obtain your key or your Google account quota, but they can use the Worker to relay their own requests and consume your Worker resources.
- CORS headers are not access control. They allow browser code from any origin to read eligible responses; they do not authenticate callers or prevent command-line clients from using the Worker.
- This is a transport proxy, not a way to grant Gemini access, quota, billing, or regional eligibility. It does not guarantee that a request will work in a location where Google does not support the API or for a key/account/model that lacks access. Follow Google's current Gemini API availability and account requirements.
- Requests and responses pass through Cloudflare and Google's API. Do not treat this Worker as a private storage location or send information you do not want processed by those services. Review their current privacy and service terms.
- The Worker forwards `OPTIONS` requests upstream; it does not implement a separate local preflight handler. CORS response headers are added to ordinary upstream responses, while the exception response adds only `Access-Control-Allow-Origin`.

### Troubleshooting

- **Mochi reports a connection or `502` error:** Confirm the Worker is deployed, the copied base URL is correct, and Cloudflare's Worker logs show no runtime error. A `502` from this script means its call to the upstream service threw an exception; an HTTP error returned by Google is forwarded with Google's status and response.
- **Google returns an API error:** Check the API key, model availability, quota, billing or account requirements, and Google's supported-region rules. The proxy cannot fix an error returned by Google.
- **Live voice does not connect:** Make sure you deployed the entire current `worker.js`, including its WebSocket relay code, and verify that the API key and selected model support the requested Live session.
- **You want a direct connection again:** Turn off **Use a custom API base URL** in Mochi Settings and save.

## توضیحات فارسی

### این Worker چه کاری انجام می‌دهد؟

این پوشه یک Cloudflare Worker اختیاری دارد که درخواست‌های موچی را به Gemini API گوگل منتقل می‌کند. کد کامل آن در فایل [`worker.js`](./worker.js) است. Worker نام میزبان درخواست را به `generativelanguage.googleapis.com` تغییر می‌دهد، اما مسیر، پارامترهای آدرس، روش درخواست، بدنه و سرآیندها را نگه می‌دارد. دو سرآیند `cf-connecting-ip` و `x-real-ip` را پیش از ارسال حذف می‌کند. اتصال WebSocket را هم در هر دو جهت عبور می‌دهد تا نشست‌های صوتی Gemini Live کار کنند. برای پاسخ‌های معمول HTTP سرآیندهای CORS اضافه می‌کند تا رابط وب بتواند پاسخ را بخواند. اگر هنگام ارتباط با سرویس بالادستی خطای اجرایی رخ دهد، پاسخ JSON با کد `502` برمی‌گرداند.

این Worker کلید API را نمی‌سازد یا ذخیره نمی‌کند، مدل را عوض نمی‌کند و تاریخچهٔ گفتگو را نگه نمی‌دارد. موچی همچنان کلید API تنظیم‌شدهٔ خود کاربر را در درخواست می‌فرستد و مقصد این Worker همان API گوگل است؛ ارائه‌دهندهٔ هوش مصنوعی دیگری نیست.

### استقرار Worker در Cloudflare

۱. وارد [داشبورد Cloudflare](https://dash.cloudflare.com/) شوید.
۲. بخش **Workers & Pages** را باز کنید و گزینهٔ ساخت Worker را انتخاب کنید. نام گزینه‌ها ممکن است با تغییر داشبورد کمی فرق کند؛ یک پروژهٔ سادهٔ «Hello World» کافی است.
۳. برای Worker نام انتخاب کنید و آن را بسازید.
۴. ویرایشگر کد Worker را باز کنید و تمام کد آغازین را با محتوای کامل فایل [`worker.js`](./worker.js) جایگزین کنید. همهٔ فایل، از جمله کد کمکی WebSocket در ابتدای آن، باید کپی شود.
۵. کد را ذخیره و Deploy کنید.
۶. صفحهٔ Worker مستقرشده را باز کنید و آدرس عمومی‌ای را که Cloudflare نشان می‌دهد کپی کنید. این آدرس معمولاً روی دامنهٔ `workers.dev` است، مگر اینکه دامنهٔ سفارشی تنظیم کرده باشید. آدرس خود Worker را بردارید، نه لینک ویرایشگر یا فایل کد را.

### وصل‌کردن موچی به Worker

۱. در موچی وارد **Settings** شوید.
۲. کلید Gemini API خود را تنظیم‌شده نگه دارید؛ Worker جایگزین کلید نیست و آن را در اختیارتان نمی‌گذارد.
۳. گزینهٔ **Use a custom API base URL** را روشن کنید.
۴. آدرس Worker مستقرشده را به‌عنوان آدرس پایه وارد و تنظیمات را ذخیره کنید. نام مدل یا مسیر endpoint مربوط به Gemini را به انتهای آدرس اضافه نکنید؛ موچی و SDK گوگل مسیر API را می‌سازند و Worker همان مسیر و پارامترهای درخواست را عبور می‌دهد.
۵. با خاموش‌کردن این گزینه و ذخیرهٔ تنظیمات، موچی دوباره مستقیم به endpoint معمول Gemini API گوگل وصل می‌شود.

### امنیت، حریم خصوصی و محدودیت‌ها

- هرکسی که آدرس عمومی Worker را داشته باشد می‌تواند از آن درخواست بفرستد. این کد احراز هویت، فهرست کاربران مجاز یا محدودیت تعداد درخواست ندارد. آدرس را عمومی فرض کنید و میزان مصرف Cloudflare را بررسی کنید؛ ترافیک ناخواسته می‌تواند از منابع Worker یا محدودیت‌های حساب Cloudflare مصرف کند.
- کلید Gemini API داخل Worker قرار ندارد. موچی آن را همراه درخواست API ارسال می‌کند. صرف دانستن آدرس Worker، کلید شما یا سهمیهٔ حساب گوگل را افشا نمی‌کند؛ با این حال دیگران می‌توانند درخواست‌های خودشان را از Worker عبور دهند و منابع Cloudflare شما را مصرف کنند.
- CORS کنترل دسترسی نیست. این سرآیندها به کدهای مرورگر از هر مبدأ اجازه می‌دهند پاسخ‌های مجاز را بخوانند، اما تماس‌گیرنده را احراز هویت نمی‌کنند و جلوی استفاده از ابزارهای خط فرمان را نمی‌گیرند.
- این Worker فقط واسطهٔ انتقال درخواست است و دسترسی، سهمیه، پرداخت یا مجازبودن منطقه‌ای Gemini را فراهم نمی‌کند. کارکرد آن در منطقه‌ای که گوگل API را پشتیبانی نمی‌کند یا برای کلید، حساب و مدلی که دسترسی ندارد تضمین‌شده نیست. شرایط و مناطق پشتیبانی‌شدهٔ فعلی Gemini API را از گوگل بررسی کنید.
- درخواست و پاسخ از Cloudflare و API گوگل عبور می‌کنند. این Worker فضای ذخیره‌سازی خصوصی نیست؛ اطلاعاتی را که نمی‌خواهید این سرویس‌ها پردازش کنند ارسال نکنید و شرایط حریم خصوصی و سرویس آن‌ها را بررسی کنید.
- درخواست‌های `OPTIONS` نیز به سرویس بالادستی فرستاده می‌شوند و Worker برای preflight پاسخ محلی جداگانه نمی‌سازد. روی پاسخ‌های معمول سرویس بالادستی سرآیندهای CORS افزوده می‌شود، اما پاسخ خطای اجرایی فقط `Access-Control-Allow-Origin` را دارد.

### رفع اشکال

- **موچی خطای اتصال یا `502` نشان می‌دهد:** مطمئن شوید Worker مستقر شده، آدرس پایه را درست کپی کرده‌اید و گزارش‌های Worker در Cloudflare خطای اجرایی ندارند. در این کد، `502` یعنی فراخوانی سرویس بالادستی با خطای اجرایی روبه‌رو شده است؛ خطای HTTP که خود گوگل برگرداند با همان کد و پاسخ گوگل عبور داده می‌شود.
- **گوگل خطای API برمی‌گرداند:** کلید API، دسترسی مدل، سهمیه، شرایط حساب یا پرداخت و پشتیبانی منطقه‌ای گوگل را بررسی کنید. پروکسی نمی‌تواند خطایی را که گوگل برگردانده برطرف کند.
- **صدای زنده وصل نمی‌شود:** مطمئن شوید کل فایل فعلی `worker.js` از جمله کد WebSocket را Deploy کرده‌اید و کلید و مدل انتخابی از نشست Live موردنظر پشتیبانی می‌کنند.
- **می‌خواهید اتصال مستقیم شود:** در Settings موچی گزینهٔ **Use a custom API base URL** را خاموش و تنظیمات را ذخیره کنید.
