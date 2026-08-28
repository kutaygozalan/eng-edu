<?php
/**
 * Template Name: Claims
 */
get_header();
?>
<?php while ( have_posts() ) : the_post(); ?>

<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Claims</span>
		<h1><?php the_title(); ?></h1>
		<p class="measure" style="color:var(--slate); font-size:1.05rem;">A claim is the moment insurance either earns its cost or doesn't. Here's what to expect and how to reach us.</p>
	</div>
</header>

<section class="section-tight">
	<div class="container">
		<div class="steps">
			<div class="step">
				<div class="step-num">01</div>
				<h3>Report it</h3>
				<p>Call your carrier's claims line or reach out to us — either way, we'll help you get it started.</p>
			</div>
			<div class="step">
				<div class="step-num">02</div>
				<h3>Get inspected</h3>
				<p>An adjuster reviews the damage or loss, usually within a few business days.</p>
			</div>
			<div class="step">
				<div class="step-num">03</div>
				<h3>Get an estimate</h3>
				<p>The carrier sends a settlement estimate based on your policy's coverage and limits.</p>
			</div>
			<div class="step">
				<div class="step-num">04</div>
				<h3>Get paid</h3>
				<p>Funds are released — directly to you, or to a contractor/shop if repairs are involved.</p>
			</div>
		</div>
	</div>
</section>

<section class="section">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">Real claims</span>
			<h2>What "fast" actually looks like</h2>
		</div>
		<div class="testimonial-grid">
			<?php foreach ( akazie_testimonials() as $t ) : ?>
			<div class="testimonial">
				<div class="stars">★★★★★</div>
				<p>&ldquo;<?php echo esc_html( $t['quote'] ); ?>&rdquo;</p>
				<div class="who"><?php echo esc_html( $t['who'] ); ?> — <?php echo esc_html( $t['meta'] ); ?></div>
			</div>
			<?php endforeach; ?>
		</div>
	</div>
</section>

<section class="section-tight">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">By carrier</span>
			<h2>Report a claim directly</h2>
			<p>Filing straight with the carrier is usually fastest. Call us any time if you'd rather have us handle it.</p>
		</div>
		<div class="table-wrap">
			<table class="compare-table">
				<thead><tr><th>Carrier</th><th>Claims line</th><th>Online</th></tr></thead>
				<tbody>
					<?php foreach ( array_slice( akazie_carriers(), 0, 5 ) as $carrier ) : ?>
					<tr>
						<td><?php echo esc_html( $carrier ); ?></td>
						<td>1-800-000-0000</td>
						<td><a href="#">File online →</a></td>
					</tr>
					<?php endforeach; ?>
				</tbody>
			</table>
		</div>
	</div>
</section>

<?php if ( get_the_content() ) : ?>
<section class="section-tight"><div class="container entry-content"><?php the_content(); ?></div></section>
<?php endif; ?>

<section class="section dark-section">
	<div class="container" style="text-align:center;">
		<h2 style="color:var(--paper); max-width:26ch; margin:0 auto 1rem;">Need help with an open claim?</h2>
		<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/contact/' ) ); ?>">Contact us</a>
	</div>
</section>

<?php endwhile; ?>
<?php get_footer(); ?>
