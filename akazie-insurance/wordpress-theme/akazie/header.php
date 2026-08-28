<?php
/**
 * Header: utility bar, logo, mega-menu nav, sticky quote CTA.
 */
?>
<!DOCTYPE html>
<html <?php language_attributes(); ?>>
<head>
	<meta charset="<?php bloginfo( 'charset' ); ?>">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<?php wp_head(); ?>
</head>
<body <?php body_class(); ?>>
<?php wp_body_open(); ?>
<a class="skip-link" href="#main">Skip to content</a>

<?php akazie_icon_defs(); ?>

<div class="utility-bar">
	<div class="container">
		<div class="u-left">
			<a href="tel:+15550192044">(555) 019-2044</a>
			<span>Mon–Fri 8a–7p</span>
		</div>
		<div class="u-right">
			<a href="<?php echo esc_url( home_url( '/client-portal/' ) ); ?>">Client login →</a>
		</div>
	</div>
</div>

<header class="site-header">
	<div class="container">
		<a class="brand" href="<?php echo esc_url( home_url( '/' ) ); ?>">
			<?php echo akazie_mark_svg(); ?>
			<span><?php bloginfo( 'name' ); ?></span>
		</a>

		<nav class="primary-nav" aria-label="Primary">
			<?php akazie_primary_nav(); ?>
		</nav>

		<div class="header-cta">
			<a class="btn btn-secondary" href="<?php echo esc_url( home_url( '/contact/' ) ); ?>">Talk to an agent</a>
			<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/get-a-quote/' ) ); ?>">Get a quote</a>
			<button class="nav-toggle" aria-label="Menu" aria-expanded="false">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
			</button>
		</div>
	</div>
</header>
<div class="nav-scrim"></div>

<main id="main">
