<?php
/**
 * Footer: sitemap columns + legal.
 */
$coverage = akazie_coverage_data();
?>
</main>

<footer class="site-footer">
	<div class="container">
		<div class="footer-top">
			<div class="footer-brand">
				<a class="brand" href="<?php echo esc_url( home_url( '/' ) ); ?>">
					<?php echo akazie_mark_svg(); ?>
					<span><?php bloginfo( 'name' ); ?></span>
				</a>
				<p>The independent agency advantage, made effortless. Licensed to place coverage across 30+ carriers.</p>
			</div>

			<?php foreach ( $coverage as $slug => $hub ) : ?>
			<div>
				<h4><a href="<?php echo esc_url( home_url( '/' . $slug . '/' ) ); ?>"><?php echo esc_html( $hub['label'] ); ?></a></h4>
				<ul>
					<?php foreach ( array_slice( $hub['products'], 0, 5 ) as $product ) : ?>
					<li><a href="<?php echo esc_url( home_url( '/' . $product['slug'] . '/' ) ); ?>"><?php echo esc_html( $product['name'] ); ?></a></li>
					<?php endforeach; ?>
				</ul>
			</div>
			<?php endforeach; ?>

			<div>
				<h4>Company</h4>
				<ul>
					<li><a href="<?php echo esc_url( home_url( '/why-akazie/' ) ); ?>">Why Akazie</a></li>
					<li><a href="<?php echo esc_url( home_url( '/claims/' ) ); ?>">Claims</a></li>
					<li><a href="<?php echo esc_url( home_url( '/learning-center/' ) ); ?>">Learning Center</a></li>
					<li><a href="<?php echo esc_url( home_url( '/contact/' ) ); ?>">Contact</a></li>
				</ul>
			</div>
		</div>

		<div class="footer-bottom">
			<span>&copy; <?php echo esc_html( date( 'Y' ) ); ?> <?php bloginfo( 'name' ); ?>. Licensed insurance agency. NPN available on request.</span>
			<div style="display:flex; gap:1.25rem;">
				<a href="<?php echo esc_url( home_url( '/privacy-policy/' ) ); ?>">Privacy policy</a>
				<a href="<?php echo esc_url( home_url( '/terms-of-use/' ) ); ?>">Terms of use</a>
				<a href="<?php echo esc_url( home_url( '/accessibility/' ) ); ?>">Accessibility</a>
			</div>
		</div>
	</div>
</footer>

<?php wp_footer(); ?>
</body>
</html>
