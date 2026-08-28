<?php
/**
 * Learning Center: blog index. Assign this as the site's "Posts page" in
 * Settings > Reading (with front-page.php handling the static home page).
 */
get_header();
?>

<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Learning Center</span>
		<h1><?php echo is_home() && ! is_front_page() ? esc_html( get_the_title( get_option( 'page_for_posts' ) ) ) : 'Learning Center'; ?></h1>
		<p class="measure" style="color:var(--slate); font-size:1.05rem;">Plain-language guides on coverage, claims, and what actually changes your rate.</p>
	</div>
</header>

<section class="section">
	<div class="container">
		<?php if ( have_posts() ) : ?>
		<div class="post-grid">
			<?php while ( have_posts() ) : the_post(); ?>
			<article class="post-card">
				<?php
				$cats = get_the_category();
				if ( ! empty( $cats ) ) :
					?>
					<span class="cat"><?php echo esc_html( $cats[0]->name ); ?></span>
				<?php endif; ?>
				<h3><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h3>
				<p><?php echo esc_html( wp_trim_words( get_the_excerpt(), 20 ) ); ?></p>
				<a class="read-more" href="<?php the_permalink(); ?>">Read more →</a>
			</article>
			<?php endwhile; ?>
		</div>

		<div class="pagination">
			<?php
			echo paginate_links( array(
				'prev_text' => '← Prev',
				'next_text' => 'Next →',
			) );
			?>
		</div>
		<?php else : ?>
		<p>No articles published yet — new posts will appear here automatically.</p>
		<?php endif; ?>
	</div>
</section>

<?php get_footer(); ?>
