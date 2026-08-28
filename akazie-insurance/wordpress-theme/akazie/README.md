# Akazie — WordPress theme

Custom theme implementing the Akazie Insurance brand guide and sitemap:
mega-menu Personal/Business/Specialty navigation, an address-first quote
flow, a KPI strip, a carrier carousel, coverage hub/product page pairs,
Claims, Why Akazie, a Learning Center blog, and working Get a Quote /
Contact forms — all in the ink/paper/ember brand system (Fraunces +
Public Sans + Space Mono).

It was built and verified against a real, running WordPress install
(WP 6.7 on SQLite) — every template, the mega-menu, the mobile nav, the
FAQ accordion, and the lead-capture form were clicked through and
screenshotted, not just written and assumed to work.

## Install

1. Zip this `akazie` folder and upload it under **Appearance → Themes → Add New → Upload Theme**, or drop it into `wp-content/themes/` directly.
2. Activate it.
3. That's it for a first look — the header/nav has a built-in fallback menu built from the coverage data below, so the mega-menu works immediately even before you build a menu in Appearance → Menus.

## Set up content (10 minutes)

The theme ships with page **templates**, not the pages themselves — create these under **Pages → Add New**, and assign the matching template in the block editor's sidebar ("Template"):

| Page title | Slug | Template |
|---|---|---|
| Home | `home` | — (leave default; `front-page.php` renders the homepage automatically) |
| Personal Insurance | `personal-insurance` | Coverage Hub |
| Business Insurance | `business-insurance` | Coverage Hub |
| Specialty Insurance | `specialty-insurance` | Coverage Hub |
| Get a Quote | `get-a-quote` | Get a Quote |
| Claims | `claims` | Claims |
| Why Akazie | `why-akazie` | Why Akazie |
| Contact | `contact` | Contact |
| Learning Center | `learning-center` | — (default; see below) |

Then, individual coverage pages (Auto, Home, General Liability, etc.) — create one page per product with **Template: Coverage Product** and a slug matching the `slug` field in `inc/site-data.php` (e.g. `auto-insurance`, `home-insurance`). The hub pages, the footer, and the fallback nav all link to these by slug automatically.

**Homepage & blog:** Settings → Reading → "Your homepage displays" → *A static page* → set **Homepage** to your `Home` page and **Posts page** to `Learning Center`. (`front-page.php` will still render the marketing homepage regardless of this setting, but WordPress needs a real front-page assignment for the Posts-page split to work — that's a WordPress quirk, not a theme requirement.)

**Menu:** Appearance → Menus → build a menu with Personal/Business/Specialty Insurance as parent items and their products nested underneath — the theme walker turns any top-level item with children into a mega-menu column automatically. Assign it to the **Primary Navigation** location. Skip this and the built-in fallback keeps working.

## Editable content without touching code

`inc/site-data.php` is the single source of truth for the coverage lists (Personal/Business/Specialty products + industries), the KPI numbers, the carrier list, and testimonials. It's plain PHP arrays so the theme has zero plugin dependencies — edit the file directly, or swap it for Advanced Custom Fields / a custom post type later without touching any template (they all call `akazie_coverage_data()`, `akazie_kpis()`, `akazie_carriers()`, `akazie_testimonials()`).

Every marketing page template also calls `the_content()` somewhere, so anything typed into the page's block editor shows up too — you don't have to edit PHP for ordinary copy changes.

## Forms

Get a Quote and Contact both post to `admin-post.php` and send real email via `wp_mail()` — no plugin required, and it's already wired end-to-end. Two things worth doing before launch:

- **Deliverability.** PHP's default `mail()` (what `wp_mail()` uses without configuration) is frequently filtered as spam or blocked outright by hosts. Install an SMTP plugin (e.g. WP Mail SMTP) and connect it to a real mail provider.
- **Spam.** There's no CAPTCHA/honeypot yet. Add one (or switch these two forms to Contact Form 7 / WPForms / Gravity Forms, which the theme's `template-parts/lead-form.php` markup can be swapped for) before the site is public.

## What's stubbed and needs real content before launch

- **Logo** — the mark from the brand guide is wired in (`inc/icons.php`), but if you get a different production file, swap the path data there.
- **Team grid** (Why Akazie) — placeholder avatars/names.
- **Carrier list & KPI numbers** (`inc/site-data.php`) — currently "Carrier One"… "30+ carriers" placeholders.
- **Testimonials** — placeholder quotes; replace with real, attributed reviews.
- **Coverage product body copy** — each product page's "What it covers" section falls back to boilerplate until you fill in the page's content editor.
- **Phone/email/address** — hardcoded in `header.php`, `footer.php`, and the Contact template; search for `555-019-2044` / `hello@akazieinsurance.com` / `123 Harbor Street` to replace everywhere.

## Not built yet (out of scope for this pass)

- A real quoting/rating engine — the "Get a quote" flow captures a lead by email, it doesn't return live rates. That requires a carrier/rating API integration.
- Client portal authentication — `/client-portal/` is a placeholder page, not a policyholder login system.
- Self-hosted fonts (Fraunces/Public Sans/Space Mono currently load from Google Fonts via `functions.php`) — fine for most sites, but self-host if the client needs to avoid third-party font requests.
