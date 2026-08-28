<?php
/**
 * Structured content for Akazie Insurance.
 *
 * These arrays back the marketing templates (coverage hubs, KPI strip,
 * carriers, testimonials). They are plain PHP for now so the theme has
 * no plugin dependency; swap this file for ACF fields or a custom post
 * type later without touching the templates that read akazie_coverage_data(),
 * akazie_kpis(), akazie_carriers(), or akazie_testimonials().
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Personal / Business / Specialty coverage hubs, keyed by page slug.
 * Each product links to a page with the matching slug if one exists,
 * otherwise it renders as plain text until that page is created.
 */
function akazie_coverage_data() {
	return array(
		'personal-insurance' => array(
			'label'   => 'Personal Insurance',
			'intro'   => "Coverage for the things you'd actually have to replace: your car, your home, the people who depend on you.",
			'products' => array(
				array( 'name' => 'Auto', 'slug' => 'auto-insurance', 'desc' => 'Liability, collision, and comprehensive coverage for one car or a whole household.' ),
				array( 'name' => 'Home', 'slug' => 'home-insurance', 'desc' => 'Rebuild cost, personal property, and liability protection for owner-occupied homes.' ),
				array( 'name' => 'Renters', 'slug' => 'renters-insurance', 'desc' => "Your belongings and liability, covered even though the building isn't yours." ),
				array( 'name' => 'Condo', 'slug' => 'condo-insurance', 'desc' => 'Fills the gap between your HOA master policy and what you actually own inside your unit.' ),
				array( 'name' => 'Landlord / Rental Property', 'slug' => 'landlord-insurance', 'desc' => 'Property and liability coverage built for a home you rent out, not live in.' ),
				array( 'name' => 'Umbrella', 'slug' => 'umbrella-insurance', 'desc' => 'Extra liability limits that sit on top of your auto and home policies.' ),
				array( 'name' => 'Life', 'slug' => 'life-insurance', 'desc' => 'Term and whole life policies sized to what your family would actually need.' ),
				array( 'name' => 'Motorcycle, Boat & RV', 'slug' => 'powersports-insurance', 'desc' => 'Coverage for what you ride and drive outside of your daily car.' ),
				array( 'name' => 'Flood', 'slug' => 'flood-insurance', 'desc' => "Coverage most home policies exclude — required in some zones, worth it in most." ),
				array( 'name' => 'Pet', 'slug' => 'pet-insurance', 'desc' => 'Vet bill reimbursement for accidents and illness.' ),
			),
		),
		'business-insurance' => array(
			'label'   => 'Business Insurance',
			'intro'   => 'From a first general liability policy to a full risk program — including coverage built around your specific industry.',
			'products' => array(
				array( 'name' => 'General Liability', 'slug' => 'general-liability-insurance', 'desc' => 'The baseline policy most clients and landlords require before you can work with them.' ),
				array( 'name' => 'Business Owners Policy', 'slug' => 'bop-insurance', 'desc' => 'Liability and property coverage bundled into one policy for small and mid-size businesses.' ),
				array( 'name' => "Workers' Compensation", 'slug' => 'workers-comp-insurance', 'desc' => 'Required in most states the moment you have employees.' ),
				array( 'name' => 'Commercial Auto', 'slug' => 'commercial-auto-insurance', 'desc' => 'Coverage for vehicles owned, leased, or driven for business.' ),
				array( 'name' => 'Professional Liability (E&O)', 'slug' => 'professional-liability-insurance', 'desc' => 'Protects against claims of mistakes, missed deadlines, or bad advice.' ),
				array( 'name' => 'Cyber Liability', 'slug' => 'cyber-liability-insurance', 'desc' => 'Coverage for data breaches, ransomware, and the notification costs that follow.' ),
				array( 'name' => 'Commercial Property', 'slug' => 'commercial-property-insurance', 'desc' => 'Protects the building, equipment, and inventory your business runs on.' ),
			),
			'industries' => array(
				'Contractors', 'Restaurants', 'Retail', 'Real Estate', 'Professional Services', 'Healthcare Practices', 'Tech & Startups',
			),
		),
		'specialty-insurance' => array(
			'label'   => 'Specialty Insurance',
			'intro'   => "For the things standard personal and business policies weren't built to cover.",
			'products' => array(
				array( 'name' => 'High-Value Home', 'slug' => 'high-value-home-insurance', 'desc' => 'Higher limits and broader coverage for homes standard policies underinsure.' ),
				array( 'name' => 'Classic & Collector Car', 'slug' => 'classic-car-insurance', 'desc' => 'Agreed-value coverage that respects what the car is actually worth.' ),
				array( 'name' => 'Wedding & Event', 'slug' => 'wedding-event-insurance', 'desc' => 'Cancellation, liability, and vendor no-show coverage for one specific day.' ),
				array( 'name' => 'Jewelry & Valuables', 'slug' => 'jewelry-valuables-insurance', 'desc' => 'Scheduled coverage for items worth more than your policy limit allows.' ),
				array( 'name' => 'Nonprofit / Association', 'slug' => 'nonprofit-insurance', 'desc' => 'Directors & officers, general liability, and event coverage for mission-driven organizations.' ),
				array( 'name' => 'Farm & Agribusiness', 'slug' => 'farm-insurance', 'desc' => 'Property, liability, and equipment coverage built around working land.' ),
			),
		),
	);
}

/**
 * Looks up which hub a product slug belongs to, e.g. 'auto-insurance' -> personal-insurance.
 * Returns array( $hub_slug, $hub, $product ) or null if the slug isn't a known product.
 */
function akazie_find_product_by_slug( $slug ) {
	foreach ( akazie_coverage_data() as $hub_slug => $hub ) {
		foreach ( $hub['products'] as $product ) {
			if ( $product['slug'] === $slug ) {
				return array( $hub_slug, $hub, $product );
			}
		}
	}
	return null;
}

/** Home-page / Why Akazie KPI strip. Replace with real figures before launch. */
function akazie_kpis() {
	return array(
		array( 'num' => '30+',  'label' => 'Carriers' ),
		array( 'num' => '22',   'label' => 'Years operating' ),
		array( 'num' => '4.9',  'label' => 'Avg. rating' ),
		array( 'num' => '48hr', 'label' => 'Avg. claim response' ),
	);
}

/** Carrier carousel. Replace names/logos with actual appointments. */
function akazie_carriers() {
	return array( 'Carrier One', 'Carrier Two', 'Carrier Three', 'Carrier Four', 'Carrier Five', 'Carrier Six', 'Carrier Seven', 'Carrier Eight' );
}

/** Homepage / claims testimonials. Replace with real, attributed reviews. */
function akazie_testimonials() {
	return array(
		array(
			'quote' => 'Filed a claim on a Tuesday, had a check by Thursday. Nobody warns you that\'s possible.',
			'who'   => 'Dana R.',
			'meta'  => 'Homeowner policy since 2021',
		),
		array(
			'quote' => 'They shopped six carriers for my restaurant and came back with real numbers, not a sales pitch.',
			'who'   => 'Marcus T.',
			'meta'  => 'Business owner, BOP + workers\' comp',
		),
		array(
			'quote' => 'Switched from a direct carrier and paid less for more coverage. Wish I\'d called sooner.',
			'who'   => 'Priya K.',
			'meta'  => 'Auto + umbrella policy',
		),
	);
}
