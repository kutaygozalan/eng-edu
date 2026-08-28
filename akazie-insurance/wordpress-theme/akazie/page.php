<?php
/**
 * Generic page template: title + editor content.
 * Dedicated templates (page-templates/) cover the marketing pages
 * that need their own layout — this one is the plain-content fallback.
 */
get_header();
?>
<?php while ( have_posts() ) : the_post(); ?>
	<header class="page-hero">
		<div class="container">
			<h1><?php the_title(); ?></h1>
		</div>
	</header>
	<article class="section">
		<div class="container">
			<div class="entry-content">
				<?php the_content(); ?>
			</div>
		</div>
	</article>
<?php endwhile; ?>
<?php get_footer(); ?>
