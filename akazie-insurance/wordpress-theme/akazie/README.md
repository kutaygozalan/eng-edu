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

That's it. Activating the theme automatically creates every page it needs (Home, the three coverage hubs, all ~24 product pages, Get a Quote, Claims, Why Akazie, Contact, Learning Center, and basic legal pages), assigns the correct template to each, sets Home/Learning Center as the front page and posts page under Settings → Reading, and builds a full Personal/Business/Specialty mega-menu assigned to the Primary Navigation location — so `/auto-insurance/`, `/get-a-quote/`, etc. all work immediately, with no manual page-building required.

This runs once (it's tracked by the `akazie_provisioned` option) and only *adds* — it matches existing pages by slug rather than creating duplicates, and it only touches Reading settings or the primary menu location if they're still unset, so it's safe to leave in place even if you're updating an already-customized site.

**If you installed an earlier version of this theme before this existed** (pages 404 despite the theme being active): update to this version and reload any `/wp-admin/` page once — provisioning also runs on `admin_init`, so it self-heals on the next admin page load without needing to reactivate. If your host still shows 404s afterward, visit **Settings → Permalinks** and click **Save Changes** once to flush rewrite rules.

## Customize the navigation (optional)

The auto-built menu is a normal WordPress menu — edit it like any other under **Appearance → Menus**. Any top-level item with children automatically renders as a mega-menu column (the custom nav walker in `functions.php` handles that), so you can freely add, remove, or reorder items without touching code.

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
