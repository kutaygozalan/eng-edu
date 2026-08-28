<?php
/**
 * Template Name: Get a Quote
 */
get_header();
?>
<?php while ( have_posts() ) : the_post(); ?>

<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Get a quote</span>
		<h1><?php the_title(); ?></h1>
		<p class="measure" style="color:var(--slate); font-size:1.05rem;">Tell us a bit about what you're covering. We'll come back with real quotes from carriers that actually write policies in your area — no obligation to buy.</p>
	</div>
</header>

<section class="section">
	<div class="container contact-grid">
		<div>
			<?php get_template_part( 'template-parts/lead-form', null, array(
				'type'          => 'quote',
				'show_coverage' => true,
				'submit_label'  => 'Request my quote',
			) ); ?>
		</div>
		<div class="contact-info">
			<h4>What happens next</h4>
			<div class="item">
				<p style="margin:0;"><strong>1. We compare.</strong><br>Your details go out to the carriers most likely to write your risk well — not every carrier we work with, just the right ones.</p>
			</div>
			<div class="item">
				<p style="margin:0;"><strong>2. We call.</strong><br>Usually the same business day, with real numbers and a plain-language rundown of what each option actually covers.</p>
			</div>
			<div class="item">
				<p style="margin:0;"><strong>3. You decide.</strong><br>No pressure, no obligation — bind a policy when you're ready, or don't.</p>
			</div>
			<div class="item">
				<h4>Prefer to talk first?</h4>
				<p style="margin:0;"><a href="tel:+15550192044" style="font-weight:600;">(555) 019-2044</a> — Mon–Fri, 8a–7p.</p>
			</div>
		</div>
	</div>
</section>

<?php endwhile; ?>
<?php get_footer(); ?>
