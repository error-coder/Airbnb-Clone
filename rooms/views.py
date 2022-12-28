from django.conf import settings
from django.utils import timezone
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.status import HTTP_204_NO_CONTENT, HTTP_400_BAD_REQUEST
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, NotAuthenticated, ParseError, PermissionDenied
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from .models import Amenity, Room
from categories.models import Category
from . import serializers
from reviews.serializers import ReviewSerializer
from medias.serializers import PhotoSerializer
from bookings.models import Booking
from bookings.serializers import PublicBookingSerializer, CreateRoomBookingSerializer

class Amenities(APIView):

    def get(self, request):
        all_amenities = Amenity.objects.all()
        serializer = serializers.AmenitySerializer(all_amenities, many=True)
        return Response(serializer.data)

    def post(self, request):
        # 사용자의 데이터로 serializer를 만들 때는 serializer는 사용자의 데이터가 amenity object가 원하는 데이터에 준수하는지 검증해야 함
        serializer = serializers.AmenitySerializer(data=request.data)
        if serializer.is_valid():
            # True일 경우 serializer.save를 해서 ModelSerializer가 자동으로 amenity를 만들게 해야함
            amenity = serializer.save()
            # 새로 만들어진 것을 serialize한 다음 리턴해줌
            return Response(serializers.AmenitySerializer(amenity).data,)
        else:
            return Response(serializer.errors, status=HTTP_400_BAD_REQUEST,)

class AmenityDetail(APIView):

    def get_object(self, pk):
        try:
            return Amenity.objects.get(pk=pk)
        except Amenity.DoesNotExist:
            raise NotFound


    def get(self, request, pk):
        amenity = self.get_object(pk)
        serializer = serializers.AmenitySerializer(amenity)
        return Response(serializer.data)

    def put(self, request, pk):
        amenity = self.get_object(pk)
        # partial은 부분 업데이트로 name 또는 description을 변경할 수 있음(둘 다X, 둘 중에 하나)
        serializer = serializers.AmenitySerializer(amenity, data=request.data, partial=True,)
        if serializer.is_valid():
            updated_amenity = serializer.save()
            return Response(serializers.AmenitySerializer(updated_amenity).data,)
        else:
            return Response(serializer.errors, status=HTTP_400_BAD_REQUEST,)

    def delete(self, request, pk):
        amenity = self.get_object(pk)
        amenity.delete()
        return Response(status=HTTP_204_NO_CONTENT)

    
class Rooms(APIView):

    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        all_rooms = Room.objects.all()
        serializer = serializers.RoomListSerializer(all_rooms, many=True, context={"request" : request},)
        return Response(serializer.data)

    def post(self, request):
            serializer = serializers.RoomDetailSerializer(data=request.data)
            if serializer.is_valid():
                category_pk = request.data.get("category")
                if not category_pk:
                    raise ParseError("Category is required.")
                try:
                    category = Category.objects.get(pk=category_pk)
                    if category.kind == Category.CategoryKindChoices.EXPERIENCES:
                        raise ParseError("The category kind should be 'rooms'.")
                except Category.DoesNotExist:
                    raise ParseError("Category not found")
                try:
                    with transaction.atomic():
                        room = serializer.save(
                            owner=request.user,
                            category=category,
                        )
                        amenities = request.data.get("amenities")
                        for amenity_pk in amenities:
                            amenity = Amenity.objects.get(pk=amenity_pk)
                            room.amenities.add(amenity)
                        serializer = serializers.RoomDetailSerializer(room)
                        return Response(serializer.data)
                except Exception:
                    raise ParseError("Amenity not found")
            else:
                return Response(serializer.errors, status=HTTP_400_BAD_REQUEST,)



class RoomDetail(APIView):

    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_object(self, pk):
        try:
            return Room.objects.get(pk=pk)
        except Room.DoesNotExist:
            raise NotFound

    def get(self, request, pk):
        room = self.get_object(pk)
        serializer = serializers.RoomDetailSerializer(room, context={"request" : request},)
        return Response(serializer.data)

    def put(self, request, pk):
        room = self.get_object(pk)
        if room.owner != request.user:
            raise PermissionDenied
        serializer = serializers.RoomDetailSerializer(room, data=request.data, partial=True,)
        if serializer.is_valid():
            category_pk = request.data.get("category")
            try:
                with transaction.atomic():
                    new_room = serializer.save()
                    if category_pk:
                        category = Category.objects.get(pk=category_pk)
                        if category.kind != Category.CategoryKindChoices.ROOM:
                            raise ParseError("The category kind should be rooms")
                        updated_room = serializer.save(category=category)
                    else:
                        updated_room = serializer.save()

                    amenities = request.data.get("amenities")
                    if amenities:
                        updated_room.amenities.clear()
                        for amenity_pk in amenities:
                            amenity = Amenity.objects.get(pk=amenity_pk)
                        new_room.amenities.add(amenity)
                        serializer = serializers.RoomDetailSerializer(new_room)
                        return Response(serializer.data)
            except Exception:
                    raise ParseError("Amenity not Found")
            else:
                return Response(serializer.errors)
        else:
            raise NotAuthenticated

    def delete(self, request, pk):

        permission_classes = [IsAuthenticatedOrReadOnly]

        room = self.get_object(pk)
        if room.owner != request.user:
            raise PermissionDenied
        room.delete()
        return Response(status=HTTP_204_NO_CONTENT)

class RoomReviews(APIView):

    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_object(self, pk):
        try:
            return Room.objects.get(pk=pk)
        except Room.DoesNotExist:
            raise NotFound

    def get(self, request, pk):
        try:
            page = request.query_params.get("page", 1) # 페이지가 url에 있지 않다면 기본값으로 페이지 1을 요청함
            page = int(page) # 페이지가 url에 있고 숫자로 되어 있다면 문자열을 실제 숫자 타입으로 바꿔야 함, 페이지가 그냥 문자라면 함수는 작동 안함
        except ValueError:
            page = 1 # url에 페이지가 없거나 유저가 잘못된 페이지를 보내는 상황이어도 페이지는 1이 됨
        page_size = 3
        start = (page - 1) * page_size
        end = start + page_size
        room = self.get_object(pk)
        serializer = ReviewSerializer(room.reviews.all()[start:end], many=True,) # 처음부터 모든 리뷰를 업로드 후 자르는 방식이 아닌, 시작과 끝 지점을 갖고 가서 db에서 살펴보는 방식
        return Response(serializer.data)

    def post(self, request, pk):
        serializer = ReviewSerializer(data=request.data)
        if serializer.is_valid():
            review = serializer.save(user=request.user, room=self.get_object(pk),)
            serializer = ReviewSerializer(review)
            return Response(serializer.data)

class RoomAmenities(APIView):

    def get_object(self, pk):
        try:
            return Room.objects.get(pk)
        except Room.DoesNotExist:
            raise NotFound

    def get(self, request, pk):
        try:
            page = request.query_params.get("page", 1)
            page = int(page)
        except ValueError:
            page = 1

        page_size = settings.PAGE_SIZE
        start = (page - 1) * page_size
        end = start + page_size
        room = self.get_object(pk)
        serializer = ReviewSerializer(room.reviews.all()[start:end], many=True,) # 처음부터 모든 리뷰를 업로드 후 자르는 방식이 아닌, 시작과 끝 지점을 갖고 가서 db에서 살펴보는 방식
        return Response(serializer.data)


class RoomPhotos(APIView):

    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_object(self, pk):
        try:
            return Room.objects.get(pk=pk)
        except Room.DoesNotExist:
            raise NotFound

    def post(self, request, pk):
        room = self.get_object(pk)
        if request.user != room.owner:
            raise PermissionDenied
        serializer = PhotoSerializer(data=request.data)
        if serializer.is_valid():
            photo = serializer.save(room=room)
            serializer = PhotoSerializer(photo)
            return Response(serializer.data)
        else:
            return Response(serializer.errors)

class RoomBookings(APIView):

    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_object(self, pk):
        try:
            return Room.objects.get(pk=pk)
        except:
            raise NotFound

    def get(self, request, pk):
        room = self.get_object(pk)
        now = timezone.localtime(timezone.now()).date()
        bookings = Booking.objects.filter(room=room, kind=Booking.BookingKindChoices.ROOM, check_in__gt=now,) 
        serializer = PublicBookingSerializer(bookings, many=True,)
        return Response(serializer.data)

    def post(self, request, pk):
        room = self.get_object(pk)
        serializer = CreateRoomBookingSerializer(data=request.data)
        if serializer.is_valid():
            booking = serializer.save(room=room, user=request.user, kind=Booking.BookingKindChoices.ROOM,)
            serializer = PublicBookingSerializer(booking)
            return Response(serializer.data)
        else:
            return Response(serializer.errors)

# Tip)
# import 할 때 django package에서 오는 것들을 우선시함
# 그 다음은 third party(이외의 것) package에서 import함 ex) rest_framework 등
# 다음은 같은 앱의 것들을 import 함, .로 시작하는 뜻은 같은 앱, 같은 폴더 안에 있다는 뜻
# 다음은 커스텀 앱에 관한 것들을 import함

 # bookings = Booking.objects.filter(room=room, kind=Booking.BookingKindChoices.ROOM, check_in__gt=now,) 
 # 우리 서버 위치의 현지시각을 localtime을 이용해 구하고 check_in 날짜가 우리가 있는 곳의 현재 날짜보다 큰 booking을 찾고 있음