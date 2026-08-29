<?php
/**
 * Akazie theme setup.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'AKAZIE_VERSION', '1.1.0' );

require get_template_directory() . '/inc/site-data.php';
require get_template_directory() . '/inc/icons.php';

/**
 * Theme support & nav menus.
 */
function akazie_setup() {
	add_theme_support( 'title-tag' );
	add_theme_support( 'post-thumbnails' );
	add_theme_support( 'html5', array( 'search-form', 'comment-form', 'comment-list', 'gallery', 'caption', 'style', 'script', 'navigation-widgets' ) );
	add_theme_support( 'automatic-feed-links' );
	add_theme_support( 'responsive-embeds' );
	add_theme_support( 'align-wide' );

	register_nav_menus( array(
		'primary' => __( 'Primary Navigation', 'akazie' ),
		'utility' => __( 'Utility Bar', 'akazie' ),
		'footer'  => __( 'Footer', 'akazie' ),
	) );
}
add_action( 'after_setup_theme', 'akazie_setup' );

/**
 * Styles & scripts.
 */
function akazie_assets() {
	wp_enqueue_style( 'akazie-fonts', 'https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,500;1,9..144,600&family=Public+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap', array(), null );
	wp_enqueue_style( 'akazie-style', get_stylesheet_uri(), array(), AKAZIE_VERSION );
	wp_enqueue_script( 'akazie-main', get_template_directory_uri() . '/assets/js/main.js', array(), AKAZIE_VERSION, true );

	if ( is_singular() && comments_open() ) {
		wp_enqueue_script( 'comment-reply' );
	}
}
add_action( 'wp_enqueue_scripts', 'akazie_assets' );

/**
 * Fallback primary menu when no menu is assigned in Appearance > Menus,
 * so the mega-menu structure is visible immediately after theme activation.
 */
function akazie_fallback_primary_menu() {
	$coverage = akazie_coverage_data();
	echo '<ul>';
	foreach ( $coverage as $slug => $hub ) {
		$page = get_page_by_path( $slug );
		$url  = $page ? get_permalink( $page ) : home_url( '/' . $slug . '/' );
		echo '<li class="nav-item"><a href="' . esc_url( $url ) . '">' . esc_html( $hub['label'] ) . '<span class="caret" aria-hidden="true"></span></a>';
		echo '<div class="mega-menu">';
		$chunks = array_chunk( $hub['products'], (int) ceil( count( $hub['products'] ) / 2 ) );
		foreach ( $chunks as $i => $chunk ) {
			echo '<ul>';
			foreach ( $chunk as $product ) {
				echo '<li><a href="' . esc_url( home_url( '/' . $product['slug'] . '/' ) ) . '">' . esc_html( $product['name'] ) . '</a></li>';
			}
			echo '</ul>';
		}
		echo '<div class="mega-cta"><a class="btn btn-primary btn-block" href="' . esc_url( home_url( '/get-a-quote/' ) ) . '">Get a quote</a></div>';
		echo '</div></li>';
	}
	echo '<li><a href="' . esc_url( home_url( '/claims/' ) ) . '">Claims</a></li>';
	echo '<li><a href="' . esc_url( home_url( '/why-akazie/' ) ) . '">Why Akazie</a></li>';
	echo '<li><a href="' . esc_url( home_url( '/learning-center/' ) ) . '">Learning Center</a></li>';
	echo '</ul>';
}

/**
 * Render the primary nav: a real WP menu if assigned, otherwise the fallback.
 */
function akazie_primary_nav() {
	if ( has_nav_menu( 'primary' ) ) {
		wp_nav_menu( array(
			'theme_location' => 'primary',
			'container'      => false,
			'items_wrap'     => '<ul>%3$s</ul>',
			'walker'         => new Akazie_Mega_Menu_Walker(),
		) );
	} else {
		akazie_fallback_primary_menu();
	}
}

/**
 * Turns top-level menu items with children into mega-menu columns automatically,
 * so editors only need to build a normal nested menu in Appearance > Menus.
 */
class Akazie_Mega_Menu_Walker extends Walker_Nav_Menu {

	public function start_lvl( &$output, $depth = 0, $args = null ) {
		if ( 0 === $depth ) {
			$output .= '<div class="mega-menu"><ul>';
		} else {
			$output .= '<ul>';
		}
	}

	public function end_lvl( &$output, $depth = 0, $args = null ) {
		if ( 0 === $depth ) {
			$output .= '</ul></div>';
		} else {
			$output .= '</ul>';
		}
	}

	public function start_el( &$output, $item, $depth = 0, $args = null, $id = 0 ) {
		if ( 0 === $depth ) {
			$has_children = in_array( 'menu-item-has-children', $item->classes, true );
			$output .= '<li class="nav-item"><a href="' . esc_url( $item->url ) . '">' . esc_html( $item->title );
			if ( $has_children ) {
				$output .= '<span class="caret" aria-hidden="true"></span>';
			}
			$output .= '</a>';
		} else {
			$output .= '<li><a href="' . esc_url( $item->url ) . '">' . esc_html( $item->title ) . '</a></li>';
		}
	}

	public function end_el( &$output, $item, $depth = 0, $args = null ) {
		// Depth > 0 items are opened and closed entirely within start_el.
		// Depth 0 items close here, after end_lvl has already closed any .mega-menu.
		if ( 0 === $depth ) {
			$output .= '</li>';
		}
	}
}

/** Register the small footer widget area used for the "Company" column, optional. */
function akazie_widgets_init() {
	register_sidebar( array(
		'name'          => __( 'Footer', 'akazie' ),
		'id'            => 'footer-1',
		'before_widget' => '<div class="widget">',
		'after_widget'  => '</div>',
		'before_title'  => '<h4>',
		'after_title'   => '</h4>',
	) );
}
add_action( 'widgets_init', 'akazie_widgets_init' );

/** Excerpt length for the Learning Center cards. */
add_filter( 'excerpt_length', function() { return 22; } );
add_filter( 'excerpt_more', function() { return '…'; } );

/**
 * Minimal working lead form handler for the Get a Quote and Contact pages,
 * so the theme sends real email via wp_mail() with no plugin required.
 * For production deliverability, pair this with an SMTP plugin (WordPress's
 * default mail() delivery is frequently blocked or spam-filtered) — or swap
 * these forms for Contact Form 7 / WPForms / Gravity Forms if preferred.
 */
function akazie_handle_lead_form() {
	if ( ! isset( $_POST['akazie_nonce'] ) || ! wp_verify_nonce( $_POST['akazie_nonce'], 'akazie_lead_form' ) ) {
		wp_die( esc_html__( 'Security check failed. Please go back and try again.', 'akazie' ) );
	}

	$form_type = isset( $_POST['form_type'] ) && 'contact' === $_POST['form_type'] ? 'contact' : 'quote';
	$name      = isset( $_POST['name'] ) ? sanitize_text_field( wp_unslash( $_POST['name'] ) ) : '';
	$email     = isset( $_POST['email'] ) ? sanitize_email( wp_unslash( $_POST['email'] ) ) : '';
	$phone     = isset( $_POST['phone'] ) ? sanitize_text_field( wp_unslash( $_POST['phone'] ) ) : '';
	$address   = isset( $_POST['address'] ) ? sanitize_text_field( wp_unslash( $_POST['address'] ) ) : '';
	$coverage  = isset( $_POST['coverage'] ) ? sanitize_text_field( wp_unslash( $_POST['coverage'] ) ) : '';
	$message   = isset( $_POST['message'] ) ? sanitize_textarea_field( wp_unslash( $_POST['message'] ) ) : '';
	$redirect  = isset( $_POST['redirect_to'] ) ? esc_url_raw( wp_unslash( $_POST['redirect_to'] ) ) : home_url( '/' );

	if ( ! $name || ! is_email( $email ) ) {
		wp_safe_redirect( add_query_arg( 'akazie_lead', 'error', $redirect ) );
		exit;
	}

	$subject = 'contact' === $form_type
		? sprintf( '[%s] New contact message from %s', get_bloginfo( 'name' ), $name )
		: sprintf( '[%s] New quote request from %s', get_bloginfo( 'name' ), $name );

	$body = "Name: {$name}\nEmail: {$email}\nPhone: {$phone}\n";
	if ( $address ) {
		$body .= "Address: {$address}\n";
	}
	if ( $coverage ) {
		$body .= "Coverage requested: {$coverage}\n";
	}
	if ( $message ) {
		$body .= "\nMessage:\n{$message}\n";
	}

	wp_mail( get_option( 'admin_email' ), $subject, $body, array( 'Reply-To: ' . $name . ' <' . $email . '>' ) );

	wp_safe_redirect( add_query_arg( 'akazie_lead', 'sent', $redirect ) );
	exit;
}
add_action( 'admin_post_nopriv_akazie_lead_form', 'akazie_handle_lead_form' );
add_action( 'admin_post_akazie_lead_form', 'akazie_handle_lead_form' );

/**
 * Auto-provisioning: creates every page the theme's templates and nav expect
 * (Home, the coverage hubs, every product, Get a Quote, Claims, Why Akazie,
 * Contact, Learning Center, legal pages), assigns the matching template to
 * each, sets Reading settings so Home/Learning Center work, and builds a
 * Primary nav menu — so a fresh WordPress install works immediately after
 * activation instead of 404ing until someone manually creates ~30 pages.
 *
 * Idempotent and additive only: matches existing pages by slug (never
 * duplicates or overwrites content) and only touches Reading settings /
 * the primary menu location if they're still unset. Runs once, gated by
 * the 'akazie_provisioned' option — safe to leave in place permanently.
 */
function akazie_provision_content() {
	if ( get_option( 'akazie_provisioned' ) ) {
		return;
	}

	$ensure_page = function( $slug, $title, $template = '' ) {
		$page = get_page_by_path( $slug );
		if ( $page ) {
			$id = $page->ID;
		} else {
			$id = wp_insert_post( array(
				'post_title'  => $title,
				'post_name'   => $slug,
				'post_type'   => 'page',
				'post_status' => 'publish',
			) );
		}
		if ( $id && ! is_wp_error( $id ) && $template ) {
			update_post_meta( $id, '_wp_page_template', $template );
		}
		return is_wp_error( $id ) ? 0 : (int) $id;
	};

	$home_id     = $ensure_page( 'home', 'Home' );
	$learning_id = $ensure_page( 'learning-center', 'Learning Center' );
	$ensure_page( 'get-a-quote', 'Get a Quote', 'page-templates/template-quote.php' );
	$ensure_page( 'claims', 'Claims', 'page-templates/template-claims.php' );
	$ensure_page( 'why-akazie', 'Why Akazie', 'page-templates/template-about.php' );
	$ensure_page( 'contact', 'Contact', 'page-templates/template-contact.php' );
	$ensure_page( 'client-portal', 'Client Portal' );
	$ensure_page( 'privacy-policy', 'Privacy Policy' );
	$ensure_page( 'terms-of-use', 'Terms of Use' );
	$ensure_page( 'accessibility', 'Accessibility Statement' );

	$coverage = akazie_coverage_data();
	$hub_ids  = array();
	foreach ( $coverage as $slug => $hub ) {
		$hub_ids[ $slug ] = $ensure_page( $slug, $hub['label'], 'page-templates/template-coverage-hub.php' );
		foreach ( $hub['products'] as $product ) {
			$ensure_page( $product['slug'], $product['name'], 'page-templates/template-coverage-product.php' );
		}
	}

	// Only set Reading options if the site doesn't already have a real front page configured.
	if ( 'page' !== get_option( 'show_on_front' ) && ! get_option( 'page_on_front' ) ) {
		update_option( 'show_on_front', 'page' );
		update_option( 'page_on_front', $home_id );
		update_option( 'page_for_posts', $learning_id );
	}

	// Only build a menu if nothing is already assigned to the primary location.
	$locations = get_nav_menu_locations();
	if ( empty( $locations['primary'] ) ) {
		$menu_id = wp_create_nav_menu( 'Primary' );
		$pos     = 1;
		foreach ( $coverage as $slug => $hub ) {
			if ( empty( $hub_ids[ $slug ] ) ) {
				continue;
			}
			$parent_item_id = wp_update_nav_menu_item( $menu_id, 0, array(
				'menu-item-title'     => $hub['label'],
				'menu-item-object'    => 'page',
				'menu-item-object-id' => $hub_ids[ $slug ],
				'menu-item-type'      => 'post_type',
				'menu-item-status'    => 'publish',
				'menu-item-position'  => $pos++,
			) );
			foreach ( $hub['products'] as $product ) {
				$product_page = get_page_by_path( $product['slug'] );
				if ( ! $product_page ) {
					continue;
				}
				wp_update_nav_menu_item( $menu_id, 0, array(
					'menu-item-title'     => $product['name'],
					'menu-item-object'    => 'page',
					'menu-item-object-id' => $product_page->ID,
					'menu-item-type'      => 'post_type',
					'menu-item-status'    => 'publish',
					'menu-item-parent-id' => $parent_item_id,
					'menu-item-position'  => $pos++,
				) );
			}
		}
		foreach ( array( 'claims' => 'Claims', 'why-akazie' => 'Why Akazie', 'learning-center' => 'Learning Center' ) as $slug => $label ) {
			$page = get_page_by_path( $slug );
			if ( ! $page ) {
				continue;
			}
			wp_update_nav_menu_item( $menu_id, 0, array(
				'menu-item-title'     => $label,
				'menu-item-object'    => 'page',
				'menu-item-object-id' => $page->ID,
				'menu-item-type'      => 'post_type',
				'menu-item-status'    => 'publish',
				'menu-item-position'  => $pos++,
			) );
		}
		$locations['primary'] = $menu_id;
		set_theme_mod( 'nav_menu_locations', $locations );
	}

	update_option( 'akazie_provisioned', AKAZIE_VERSION );
}
add_action( 'after_switch_theme', 'akazie_provision_content' );
add_action( 'admin_init', 'akazie_provision_content' );
