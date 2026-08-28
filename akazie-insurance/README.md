# Akazie Insurance — Website Deliverables

Deliverables for the new Akazie Insurance website, based on the competitive
notes in `Insurance_Agency_Websites.docx` (Ferguson McGuire, Squeri, Keating,
Cusmano, Malloy, Newberry, NT Insurance, Colonial Air) plus the direct
design references supplied for tone (Lemonade, Kin, Clearcover, Oscar
Health, Hippo, Ladder Life, Next Insurance, Policygenius).

Rendered, interactive versions of both documents:

- **Brand guide** — positioning, voice & tone, logo direction, color,
  type, imagery/icon rules, and the UI patterns pulled from the
  competitive review: https://claude.ai/code/artifact/50eb0294-1afc-4795-8670-112becaf860d
- **Sitemap** — full site architecture, nine primary sections, and which
  page carries which borrowed UX pattern: https://claude.ai/code/artifact/918214aa-081b-4ebd-adec-d42af1f55648

Plain-text summaries of both are in this folder (`brand-guide.md`,
`sitemap.md`) for anyone without artifact access.

## Logo

The real logo (a circular tree/canopy mark, supplied as a PDF) is now
incorporated into the brand guide's Logo section, recolored to the
brand's ink/paper palette per instruction (the original gold was
dropped). See `brand-guide.md` for the colorway and usage rules.

## WordPress theme

`wordpress-theme/akazie/` is a complete, working custom WordPress theme
implementing the sitemap and brand guide — mega-menu Personal/Business/
Specialty navigation, an address-first quote flow, a KPI strip, a
carrier carousel, paired coverage hub/product page templates, Claims,
Why Akazie, a Learning Center blog, and working Get a Quote / Contact
forms (real `wp_mail()` submission, no plugin required).

It was built against and verified on a real, running WordPress 6.7
instance (via WP-CLI + the SQLite integration, so no MySQL was needed
for testing) — every template, the mega-menu, the mobile nav, the FAQ
accordion, and the lead form were actually clicked through and
screenshotted, not just written and assumed correct. See
`wordpress-theme/akazie/README.md` for installation and content setup
(zip-and-upload, then a ~10-minute page/menu setup checklist), and for
what's still a placeholder (team photos, carrier list, testimonials,
phone/email) versus genuinely out of scope (a live rating engine,
policyholder portal auth).
