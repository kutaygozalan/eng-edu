<?php
/**
 * Shared quote/contact form, posted to admin-post.php -> akazie_handle_lead_form().
 * $args: 'type' => 'quote'|'contact', 'show_coverage' => bool, 'submit_label' => string.
 */
$args          = isset( $args ) ? $args : array();
$type          = isset( $args['type'] ) ? $args['type'] : 'quote';
$show_coverage = ! empty( $args['show_coverage'] );
$submit_label  = isset( $args['submit_label'] ) ? $args['submit_label'] : 'Send';

$sent = isset( $_GET['akazie_lead'] ) ? sanitize_text_field( wp_unslash( $_GET['akazie_lead'] ) ) : '';
?>
<?php if ( 'sent' === $sent ) : ?>
	<div class="form-card" role="status">
		<h3>Thanks — that's in.</h3>
		<p style="color:var(--slate); margin:0;">Someone from Akazie will follow up shortly, usually the same business day.</p>
	</div>
<?php else : ?>
<form class="form-card" method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
	<?php if ( 'error' === $sent ) : ?>
	<p style="color:var(--ember); font-weight:600;">Please double-check your name and email and try again.</p>
	<?php endif; ?>
	<input type="hidden" name="action" value="akazie_lead_form">
	<input type="hidden" name="form_type" value="<?php echo esc_attr( $type ); ?>">
	<input type="hidden" name="redirect_to" value="<?php echo esc_url( get_permalink() ); ?>">
	<?php wp_nonce_field( 'akazie_lead_form', 'akazie_nonce' ); ?>

	<div class="field-grid">
		<div class="field-row">
			<label class="field-label" for="lf-name">Full name</label>
			<input class="field-input" id="lf-name" name="name" type="text" required>
		</div>
		<div class="field-row">
			<label class="field-label" for="lf-email">Email</label>
			<input class="field-input" id="lf-email" name="email" type="email" required>
		</div>
	</div>
	<div class="field-grid">
		<div class="field-row">
			<label class="field-label" for="lf-phone">Phone</label>
			<input class="field-input" id="lf-phone" name="phone" type="tel">
		</div>
		<div class="field-row">
			<label class="field-label" for="lf-address"><?php echo 'quote' === $type ? 'Address' : 'City &amp; state'; ?></label>
			<input class="field-input" id="lf-address" name="address" type="text">
		</div>
	</div>

	<?php if ( $show_coverage ) : ?>
	<div class="field-row">
		<label class="field-label" for="lf-coverage">What are you looking to cover?</label>
		<select class="field-select" id="lf-coverage" name="coverage">
			<option value="">Select one</option>
			<?php foreach ( akazie_coverage_data() as $hub ) : ?>
				<optgroup label="<?php echo esc_attr( $hub['label'] ); ?>">
					<?php foreach ( $hub['products'] as $product ) : ?>
					<option value="<?php echo esc_attr( $product['name'] ); ?>"><?php echo esc_html( $product['name'] ); ?></option>
					<?php endforeach; ?>
				</optgroup>
			<?php endforeach; ?>
		</select>
	</div>
	<?php endif; ?>

	<div class="field-row">
		<label class="field-label" for="lf-message"><?php echo 'quote' === $type ? 'Anything else we should know?' : 'Message'; ?></label>
		<textarea class="field-textarea" id="lf-message" name="message"></textarea>
	</div>

	<button class="btn btn-primary btn-block" type="submit"><?php echo esc_html( $submit_label ); ?></button>
</form>
<?php endif; ?>
