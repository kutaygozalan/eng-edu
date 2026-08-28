<?php
/**
 * 404.
 */
get_header();
?>
<section class="error-404">
	<div class="container">
		<?php echo akazie_mark_svg( 'error-404-mark' ); ?>
		<h1>404</h1>
		<p class="measure" style="margin:0 auto 1.5rem; color:var(--slate);">That page moved, or never existed. Try the quote flow or head back home.</p>
		<div style="display:flex; gap:1rem; justify-content:center; flex-wrap:wrap;">
			<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
			<a class="btn btn-secondary" href="<?php echo esc_url( home_url( '/' ) ); ?>">Back home</a>
		</div>
	</div>
</section>
<?php get_footer(); ?>
