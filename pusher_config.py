import os
import pusher
from flask import jsonify, session, request

# Initialize Pusher
pusher_client = pusher.Pusher(
    app_id=os.getenv('PUSHER_APP_ID'),
    key=os.getenv('PUSHER_KEY'),
    secret=os.getenv('PUSHER_SECRET'),
    cluster=os.getenv('PUSHER_CLUSTER', 'us2'),
    ssl=True
)

def init_pusher(app):
    @app.route('/pusher/auth', methods=['POST'])
    def pusher_auth():
        try:
            user_id = session.get('user_id')
            user_role = session.get('user_role')
            
            if not user_id or not user_role:
                return jsonify({'error': 'Unauthorized'}), 403

            # Pusher sends data as form data, not JSON
            socket_id = request.form.get('socket_id')
            channel_name = request.form.get('channel_name')

            if not socket_id or not channel_name:
                return jsonify({'error': 'Missing socket_id or channel_name'}), 400

            # Verify channel name format to match frontend
            expected_channel = f'private-notifications-{user_id}-{user_role}'
            if channel_name != expected_channel:
                app.logger.warning(f"Unauthorized channel access attempt. Expected: {expected_channel}, Got: {channel_name}")
                return jsonify({'error': 'Unauthorized channel'}), 403

            auth = pusher_client.authenticate(
                channel=channel_name,
                socket_id=socket_id,
                user_data={'user_id': str(user_id), 'user_role': user_role}
            )
            return jsonify(auth)
        except Exception as e:
            app.logger.error(f"Pusher auth error: {str(e)}")
            return jsonify({'error': str(e)}), 500

    return pusher_client 