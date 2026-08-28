<?php
/**
 * Single Learning Center article.
 */
get_header();
?>
<?php while ( have_posts() ) : the_post(); ?>
<article class="single-article">
	<header class="page-hero">
		<div class="container">
			<span class="eyebrow"><?php the_category( ', ' ); ?></span>
			<h1><?php the_title(); ?></h1>
			<p class="entry-meta">By <?php the_author(); ?> · <?php echo get_the_date(); ?></p>
		</div>
	</header>

	<div class="section">
		<div class="container">
			<?php if ( has_post_thumbnail() ) : ?>
				<div style="margin-bottom:2rem;"><?php the_post_thumbnail( 'large' ); ?></div>
			<?php endif; ?>
			<div class="entry-content">
				<?php the_content(); ?>
			</div>

			<div class="calc-teaser">
				<div>
					<h3 style="margin:0 0 0.3rem;">Not sure what this means for your rate?</h3>
					<p style="margin:0; color:var(--slate);">Get a real quote and find out in about two minutes.</p>
				</div>
				<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
			</div>

			<?php if ( comments_open() || get_comments_number() ) : ?>
				<?php comments_template(); ?>
			<?php endif; ?>
		</div>
	</div>
</article>
<?php endwhile; ?>
<?php get_footer(); ?>
