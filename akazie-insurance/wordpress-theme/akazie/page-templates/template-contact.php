<?php
/**
 * Template Name: Contact
 */
get_header();
?>
<?php while ( have_posts() ) : the_post(); ?>

<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Contact</span>
		<h1><?php the_title(); ?></h1>
	</div>
</header>

<section class="section">
	<div class="container contact-grid">
		<div class="contact-info">
			<div class="item">
				<h4>Phone</h4>
				<p style="margin:0;"><a href="tel:+15550192044" style="font-weight:600; font-size:1.1rem;">(555) 019-2044</a><br>Mon–Fri, 8a–7p</p>
			</div>
			<div class="item">
				<h4>Email</h4>
				<p style="margin:0;"><a href="mailto:hello@akazieinsurance.com" style="font-weight:600;">hello@akazieinsurance.com</a></p>
			</div>
			<div class="item">
				<h4>Claims</h4>
				<p style="margin:0;">Filing a claim? Visit the <a href="<?php echo esc_url( home_url( '/claims/' ) ); ?>">Claims</a> page for carrier-specific instructions.</p>
			</div>
			<div class="item">
				<h4>Office</h4>
				<p style="margin:0;">123 Harbor Street, Suite 200<br>Your City, ST 00000</p>
			</div>
			<?php if ( get_the_content() ) : ?>
			<div class="entry-content" style="margin-top:1.5rem;"><?php the_content(); ?></div>
			<?php endif; ?>
		</div>
		<div>
			<?php get_template_part( 'template-parts/lead-form', null, array(
				'type'          => 'contact',
				'show_coverage' => false,
				'submit_label'  => 'Send message',
			) ); ?>
		</div>
	</div>
</section>

<?php endwhile; ?>
<?php get_footer(); ?>
