<?php
/**
 * Search results.
 */
get_header();
?>
<header class="page-hero">
	<div class="container">
		<span class="eyebrow">Search</span>
		<h1>Results for &ldquo;<?php echo esc_html( get_search_query() ); ?>&rdquo;</h1>
	</div>
</header>

<section class="section">
	<div class="container">
		<?php if ( have_posts() ) : ?>
		<div class="post-grid">
			<?php while ( have_posts() ) : the_post(); ?>
			<article class="post-card">
				<span class="cat"><?php echo esc_html( get_post_type() ); ?></span>
				<h3><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h3>
				<p><?php echo esc_html( wp_trim_words( get_the_excerpt(), 20 ) ); ?></p>
				<a class="read-more" href="<?php the_permalink(); ?>">View →</a>
			</article>
			<?php endwhile; ?>
		</div>
		<div class="pagination"><?php echo paginate_links( array( 'prev_text' => '← Prev', 'next_text' => 'Next →' ) ); ?></div>
		<?php else : ?>
		<p>Nothing matched that search. Try the <a href="<?php echo esc_url( home_url( '/learning-center/' ) ); ?>">Learning Center</a> or <a href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">get a quote</a> instead.</p>
		<?php endif; ?>
	</div>
</section>
<?php get_footer(); ?>
