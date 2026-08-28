<?php
/**
 * Template Name: Why Akazie
 */
get_header();

$team = array(
	array( 'name' => 'Add your team', 'role' => 'Replace this grid with real headshots &amp; bios' ),
	array( 'name' => 'Add your team', 'role' => 'Replace this grid with real headshots &amp; bios' ),
	array( 'name' => 'Add your team', 'role' => 'Replace this grid with real headshots &amp; bios' ),
	array( 'name' => 'Add your team', 'role' => 'Replace this grid with real headshots &amp; bios' ),
);
?>
<?php while ( have_posts() ) : the_post(); ?>

<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Why Akazie</span>
		<h1><?php the_title(); ?></h1>
		<p class="measure" style="color:var(--slate); font-size:1.05rem;">The independent agency advantage, made effortless — one conversation, thirty-plus carriers, and someone who stays on your side after the policy is bound.</p>
	</div>
</header>

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
		<?php if ( get_the_content() ) : ?>
		<div class="entry-content measure"><?php the_content(); ?></div>
		<?php else : ?>
		<div class="entry-content measure">
			<h2>Our story</h2>
			<p>Add your agency's founding story here — why it started, what changed, and why an independent model still matters. This section pulls from the page editor, so it's yours to write without touching the theme.</p>
		</div>
		<?php endif; ?>
	</div>
</section>

<section class="section-tight">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">Team</span>
			<h2>Meet the team</h2>
		</div>
		<div class="team-grid">
			<?php foreach ( $team as $person ) : ?>
			<div class="team-card">
				<div class="avatar"><?php echo akazie_icon( 'ic-business', 'ic' ); ?></div>
				<h3><?php echo esc_html( $person['name'] ); ?></h3>
				<p><?php echo wp_kses_post( $person['role'] ); ?></p>
			</div>
			<?php endforeach; ?>
		</div>
	</div>
</section>

<section class="section">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">Carriers we represent</span>
			<h2>The shelf we shop from</h2>
		</div>
		<div class="carrier-strip">
			<?php foreach ( akazie_carriers() as $carrier ) : ?>
			<div class="carrier-chip"><?php echo esc_html( strtoupper( $carrier ) ); ?></div>
			<?php endforeach; ?>
		</div>
	</div>
</section>

<section class="section-tight">
	<div class="container">
		<div class="section-head">
			<span class="eyebrow">Reviews</span>
			<h2>What clients say</h2>
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

<section class="section dark-section">
	<div class="container" style="text-align:center;">
		<h2 style="color:var(--paper); max-width:26ch; margin:0 auto 1rem;">Curious what independent actually gets you?</h2>
		<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
	</div>
</section>

<?php endwhile; ?>
<?php get_footer(); ?>
