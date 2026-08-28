<?php
/**
 * Home page.
 */
get_header();
$coverage = akazie_coverage_data();
?>

<section class="hero">
	<div class="container">
		<div>
			<span class="eyebrow">Personal · Business · Specialty</span>
			<h1>Insurance from an agency that actually shops around.</h1>
			<p class="dek">Tell us your address. We'll compare quotes across 30+ carriers and show you real numbers — not a sales pitch.</p>
			<form class="quote-start" data-action="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">
				<label class="screen-reader-text" for="hero-address">Your address</label>
				<input id="hero-address" type="text" name="address" placeholder="Enter your address" autocomplete="street-address">
				<button type="submit">Start →</button>
			</form>
			<p class="hero-note">No spam, no obligation — just quotes.</p>
		</div>
		<div class="hero-art">
			<?php echo akazie_mark_svg(); ?>
		</div>
	</div>
</section>

<section class="section-tight">
	<div class="container">
		<div class="kpi-strip">
			<?php foreach ( akazie_kpis() as $kpi ) : ?>
			<div class="cell">
				<div class="num"><?php echo esc_html( $kpi['num'] ); ?></div>
				<div class="label"><?php echo esc_html( $kpi['label'] ); ?></div>
			</div>
			<?php endforeach; ?>
		</div>
	</div>
</section>

<section class="section">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">Coverage</span>
			<h2>Whatever you're protecting, there's a policy for it.</h2>
			<p>Personal, business, and specialty lines — placed with the carrier that actually fits, not the one we happen to sell.</p>
		</div>
		<div class="board-grid">
			<a class="board-card" href="<?php echo esc_url( home_url( '/auto-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-auto' ); ?>
				<h3>Auto</h3>
				<p>Liability, collision &amp; comprehensive.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/home-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-home' ); ?>
				<h3>Home</h3>
				<p>Rebuild cost &amp; personal property.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/life-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-life' ); ?>
				<h3>Life</h3>
				<p>Term &amp; whole life, sized to your family.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/business-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-business' ); ?>
				<h3>Business</h3>
				<p>From a first GL policy to a full risk program.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/umbrella-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-umbrella' ); ?>
				<h3>Umbrella</h3>
				<p>Extra liability limits, one policy.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/specialty-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-specialty' ); ?>
				<h3>Specialty</h3>
				<p>Collector cars, weddings, valuables &amp; more.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/renters-insurance/' ) ); ?>">
				<?php echo akazie_icon( 'ic-home' ); ?>
				<h3>Renters</h3>
				<p>Your belongings, covered.</p>
			</a>
			<a class="board-card" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">
				<?php echo akazie_icon( 'ic-check' ); ?>
				<h3>Not sure what you need?</h3>
				<p>Start a quote and we'll help you figure it out.</p>
			</a>
		</div>
	</div>
</section>

<section class="section dark-section">
	<div class="container">
		<div class="split">
			<div>
				<span class="eyebrow" style="color:var(--gold);">The independent agency advantage</span>
				<h2 style="color:var(--paper);">One conversation. Thirty-plus carriers.</h2>
				<p class="slate-on-dark measure">A captive agent sells you one company's product. We work for you — comparing coverage and price across the carriers that actually write policies in your area, then standing behind the recommendation.</p>
				<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/why-akazie/' ) ); ?>">See why it matters</a>
			</div>
			<div class="split-art">
				<span class="num">30+</span>
				<p style="color:var(--paper); margin:0;">carriers on our shelf — home, auto, business, and specialty markets included.</p>
			</div>
		</div>
	</div>
</section>

<section class="section-tight">
	<div class="container">
		<div class="section-head" style="margin-bottom:1.5rem;">
			<span class="eyebrow">Carriers we place with</span>
		</div>
		<div class="carrier-strip">
			<?php foreach ( akazie_carriers() as $carrier ) : ?>
			<div class="carrier-chip"><?php echo esc_html( strtoupper( $carrier ) ); ?></div>
			<?php endforeach; ?>
		</div>
	</div>
</section>

<section class="section">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">Claims</span>
			<h2>Filed fast. Paid fast.</h2>
			<p>We stay involved after the sale — advocating on your claim, not disappearing once the policy is bound.</p>
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
		<div class="split reverse" style="align-items:center;">
			<div>
				<span class="eyebrow">Get a quote</span>
				<h2>Two minutes now, or a phone tag forever.</h2>
				<p class="measure" style="color:var(--slate);">Start with your address. We'll follow up with real quotes from carriers that actually cover it — no obligation to buy.</p>
				<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
			</div>
			<div class="split-art" style="background:var(--sea); color:var(--ink);">
				<span class="num" style="color:var(--ember);">2 min</span>
				<p style="margin:0;">is all the first step takes.</p>
			</div>
		</div>
	</div>
</section>

<?php get_footer(); ?>
