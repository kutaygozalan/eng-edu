<?php
/**
 * Template Name: Coverage Product
 *
 * Assign this to individual coverage pages (Auto, Home, General Liability,
 * etc). The page's slug is matched against akazie_coverage_data() to find
 * its parent hub, description, and sibling products for the ribbon — so
 * create the page with the same slug used in inc/site-data.php.
 */
get_header();

$slug  = get_post_field( 'post_name' );
$match = akazie_find_product_by_slug( $slug );
list( $hub_slug, $hub, $product ) = $match ? $match : array( null, null, null );
?>
<?php while ( have_posts() ) : the_post(); ?>

<section class="hero">
	<div class="container">
		<div>
			<?php if ( $hub ) : ?>
			<span class="eyebrow"><a href="<?php echo esc_url( home_url( '/' . $hub_slug . '/' ) ); ?>" style="color:inherit;"><?php echo esc_html( $hub['label'] ); ?></a></span>
			<?php endif; ?>
			<h1><?php the_title(); ?> Insurance</h1>
			<p class="dek"><?php echo esc_html( $product ? $product['desc'] : get_the_excerpt() ); ?></p>
			<form class="quote-start" data-action="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">
				<label class="screen-reader-text" for="product-address">Your address</label>
				<input id="product-address" type="text" name="address" placeholder="Enter your address" autocomplete="street-address">
				<button type="submit">Get a quote →</button>
			</form>
		</div>
		<div class="hero-art">
			<?php echo akazie_mark_svg(); ?>
		</div>
	</div>
</section>

<section class="section-tight">
	<div class="container">
		<?php if ( get_the_content() ) : ?>
		<div class="entry-content">
			<?php the_content(); ?>
		</div>
		<?php else : ?>
		<div class="entry-content">
			<h2>What it covers</h2>
			<p>Add the specifics for this policy here — typical limits, what's included, and the most common add-ons. This section pulls straight from the page's content editor, so it can be written and revised without touching the theme.</p>
		</div>
		<?php endif; ?>

		<div class="faq" style="margin-top:2.5rem;">
			<div class="faq-item">
				<button class="faq-q" aria-expanded="false">How fast can I get a quote?<span class="plus" aria-hidden="true"></span></button>
				<div class="faq-a"><p>Most quotes come back the same day once we have your address and a few basic details — often within minutes for auto and home.</p></div>
			</div>
			<div class="faq-item">
				<button class="faq-q" aria-expanded="false">Can I bundle this with other policies?<span class="plus" aria-hidden="true"></span></button>
				<div class="faq-a"><p>Yes — bundling with home, auto, or umbrella coverage usually lowers the rate on both policies. Ask your agent when you request a quote.</p></div>
			</div>
			<div class="faq-item">
				<button class="faq-q" aria-expanded="false">What happens if I need to file a claim?<span class="plus" aria-hidden="true"></span></button>
				<div class="faq-a"><p>Call us or the carrier directly — either way, we stay involved to help move things along. See the <a href="<?php echo esc_url( home_url( '/claims/' ) ); ?>">Claims</a> page for what to expect.</p></div>
			</div>
		</div>
	</div>
</section>

<?php if ( $hub ) : ?>
<section class="section-tight">
	<div class="container">
		<h3 style="font-size:1.1rem; margin-bottom:1rem;">More <?php echo esc_html( strtolower( $hub['label'] ) ); ?></h3>
		<div class="ribbon">
			<?php foreach ( $hub['products'] as $sibling ) : ?>
			<a class="<?php echo $sibling['slug'] === $slug ? 'is-current' : ''; ?>" href="<?php echo esc_url( home_url( '/' . $sibling['slug'] . '/' ) ); ?>"><?php echo esc_html( $sibling['name'] ); ?></a>
			<?php endforeach; ?>
		</div>
	</div>
</section>
<?php endif; ?>

<section class="section dark-section">
	<div class="container" style="text-align:center;">
		<h2 style="color:var(--paper); max-width:26ch; margin:0 auto 1rem;">See what <?php the_title(); ?> actually costs.</h2>
		<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
	</div>
</section>

<?php endwhile; ?>
<?php get_footer(); ?>
