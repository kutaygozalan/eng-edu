<?php
/**
 * Template Name: Coverage Hub (Personal / Business / Specialty)
 *
 * Assign this to the three category pages: Personal Insurance,
 * Business Insurance, Specialty Insurance. The page's own slug picks
 * which entry of akazie_coverage_data() to render, so create the page
 * with a matching slug (e.g. /personal-insurance/) before assigning it.
 */
get_header();

$all      = akazie_coverage_data();
$slug     = get_post_field( 'post_name' );
$hub      = isset( $all[ $slug ] ) ? $all[ $slug ] : reset( $all );
$hub_slug = isset( $all[ $slug ] ) ? $slug : key( $all );
?>
<?php while ( have_posts() ) : the_post(); ?>

<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Coverage</span>
		<h1><?php the_title(); ?></h1>
		<p class="measure" style="color:var(--slate); font-size:1.05rem;"><?php echo esc_html( $hub['intro'] ); ?></p>
	</div>
</header>

<section class="section">
	<div class="container">
		<div class="ribbon" style="margin-bottom:2.5rem;">
			<?php foreach ( $all as $other_slug => $other_hub ) : ?>
			<a class="<?php echo $other_slug === $hub_slug ? 'is-current' : ''; ?>" href="<?php echo esc_url( home_url( '/' . $other_slug . '/' ) ); ?>"><?php echo esc_html( $other_hub['label'] ); ?></a>
			<?php endforeach; ?>
		</div>

		<div class="board-grid">
			<?php foreach ( $hub['products'] as $product ) : ?>
			<a class="board-card" href="<?php echo esc_url( home_url( '/' . $product['slug'] . '/' ) ); ?>">
				<h3><?php echo esc_html( $product['name'] ); ?></h3>
				<p><?php echo esc_html( $product['desc'] ); ?></p>
			</a>
			<?php endforeach; ?>
		</div>

		<?php if ( ! empty( $hub['industries'] ) ) : ?>
		<div class="section-tight" style="padding-bottom:0;">
			<h3 style="font-size:1.1rem; margin-bottom:1rem;">Built for your industry</h3>
			<div class="ribbon">
				<?php foreach ( $hub['industries'] as $industry ) : ?>
				<a href="<?php echo esc_url( home_url( '/get-a-quote/?industry=' . rawurlencode( $industry ) ) ); ?>"><?php echo esc_html( $industry ); ?></a>
				<?php endforeach; ?>
			</div>
		</div>
		<?php endif; ?>

		<?php if ( get_the_content() ) : ?>
		<div class="entry-content section-tight" style="padding-left:0; padding-right:0;">
			<?php the_content(); ?>
		</div>
		<?php endif; ?>
	</div>
</section>

<section class="section dark-section">
	<div class="container" style="text-align:center;">
		<h2 style="color:var(--paper); max-width:26ch; margin:0 auto 1rem;">Ready to see real numbers for <?php echo esc_html( strtolower( $hub['label'] ) ); ?>?</h2>
		<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
	</div>
</section>

<?php endwhile; ?>
<?php get_footer(); ?>
