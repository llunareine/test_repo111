import os
import secrets

import django_filters
from django.http import HttpResponseRedirect, Http404
from rest_framework.decorators import permission_classes, api_view
from rest_framework import generics
from . import models, filter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .serializers import *
from gpt_config import chat_query
from gpt_test_config import test_query
from rest_framework import status, filters
from .models import Test, Question, QuestionOption, FavoriteCourse
from django.contrib.auth.models import Group
from django.shortcuts import reverse, redirect
from django.shortcuts import get_object_or_404


############################### USER ###########################################
class UserRegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        response = super(UserRegistrationView, self).create(request, *args, **kwargs)
        if response.status_code == 201:
            return HttpResponseRedirect('/api/auth/login/')
        return response


class UserProfileView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        return response

    def destroy(self, request, *args, **kwargs):
        response = super().destroy(request, *args, **kwargs)
        return response


############################ CHAT ######################################
class ChatQueryView(generics.CreateAPIView):
    queryset = models.ChatHistory.objects.all()
    serializer_class = ChatHistorySerizlizer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        user_query = request.data.get('content')
        gpt_response = chat_query(user_query)
        chat_history = models.ChatHistory(content=user_query, chat_answer=gpt_response, user=request.user)
        chat_history.save()

        serializer = ChatHistorySerizlizer(chat_history)

        return Response({
            "serializer": serializer.data,
            "chat_answer": gpt_response
        })


class ChatHistoryAll(generics.ListAPIView):
    queryset = models.ChatHistory.objects.order_by("-created_date")
    serializer_class = ChatHistorySerizlizerGET
    permission_classes([IsAuthenticated])

    def list(self, request, *args, **kwargs):
        user = self.request.user
        if user.is_authenticated:
            queryset = models.ChatHistory.objects.filter(user=user).order_by("-created_date")
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
        else:
            return Response({"USER": "UNAUTHORIZED"}, status=status.HTTP_401_UNAUTHORIZED)


class ChatHistoryDetailDelete(generics.RetrieveDestroyAPIView):
    queryset = models.ChatHistory.objects.all()
    serializer_class = ChatHistorySerizlizerGET
    permission_classes([IsAuthenticated])

    def get_object(self):
        pk = self.kwargs.get('pk')
        try:
            chat_history = self.queryset.get(pk=pk)
            if self.request.user == chat_history.user:
                return chat_history
            else:
                return
        except models.ChatHistory.DoesNotExist:
            return


############################### TEST ###########################################
class TestCreateView(generics.CreateAPIView):
    queryset = Test.objects.all()
    serializer_class = TestSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        my_text = request.data.get('my_text')
        if my_text is None:
            return Response({'error': 'my_text is required'}, status=status.HTTP_400_BAD_REQUEST)

        question_title = Test.objects.create(my_text=my_text, user=request.user)

        questions = test_query(my_text)
        if questions is None or not isinstance(questions, list):
            return Response({'error': 'Invalid questions data'}, status=status.HTTP_400_BAD_REQUEST)

        for ques in questions:
            question = ques.get("question")
            options = ques.get("options")
            a = options.get("A")
            b = options.get("B")
            c = options.get("C")
            d = options.get("D")
            correct_answer = ques.get("correct_answer")

            q = Question.objects.create(test=question_title, text=question)
            opt = QuestionOption.objects.create(question=q, text=a, is_correct=True)
            opt1 = QuestionOption.objects.create(question=q, text=b)
            opt2 = QuestionOption.objects.create(question=q, text=c)
            opt3 = QuestionOption.objects.create(question=q, text=d)

        serializer = TestSerializer(question_title)
        return Response(serializer.data)


class TestAll(generics.ListAPIView):
    serializer_class = TestSerializerGET
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Test.objects.filter(user=self.request.user)


class TestDeleteView(generics.RetrieveDestroyAPIView):
    serializer_class = TestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Test.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'status': 'Test deleted'}, status=status.HTTP_204_NO_CONTENT)


############################### COURSE ###########################################
class CoursesListView(generics.ListAPIView):
    queryset = models.Course.objects.all()
    serializer_class = CourseSerializerGET
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, django_filters.rest_framework.DjangoFilterBackend]
    search_fields = ["name"]
    ordering_fields = ["price", "name"]
    filter_class = filter.CourseFilter

    def get_queryset(self):
        queryset = models.Course.objects.all()
        choices_param = self.request.query_params.get('category')
        if choices_param:
            category = str(choices_param).lower().capitalize()
            queryset = queryset.filter(category=category)

        return queryset


class CourseQueryView(generics.CreateAPIView):
    queryset = models.Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = CourseSerializer(data=request.data)
        if serializer.is_valid():
            self.process_course_serializer(serializer, request.data)
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def process_course_serializer(self, serializer, data):
        image = data.get("img")
        category = data.get("category")
        group_name = serializer.validated_data.get("name")

        if image:
            self.upload_image(serializer, image)

        if category == "Free":
            serializer.validated_data["price"] = 0
            self.delete_existing_group(group_name)
        elif category == "Paid":
            self.create_or_get_group(group_name)

    @staticmethod
    def upload_image(serializer, image):
        image_name = f"{secrets.token_hex(10)}.{image.name.split('.')[-1]}"
        image_path = os.path.join("media/images", image_name)
        with open(image_path, "wb") as image_file:
            for chunk in image.chunks():
                image_file.write(chunk)
        serializer.validated_data["img"] = image_path.replace("media/", "")

    @staticmethod
    def create_or_get_group(group_name):
        try:
            Group.objects.get(name=group_name)
        except Group.DoesNotExist:
            new_group = Group(name=group_name)
            new_group.save()

    @staticmethod
    def delete_existing_group(group_name):
        try:
            existing_group = Group.objects.get(name=group_name)
            existing_group.delete()
        except Group.DoesNotExist:
            pass


class CourseDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = models.Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return get_object_or_404(self.queryset, pk=self.kwargs.get("pk"), user=self.request.user)

    def delete(self, request, *args, **kwargs):
        course = self.get_object()
        course.delete()
        return Response({"status": "Course successfully deleted"}, status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        course = self.get_object()
        serializer = CourseSerializer(course, data=request.data, partial=True)  # Allow partial updates

        if serializer.is_valid():
            self.process_course_serializer(serializer, request.data)
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def process_course_serializer(self, serializer, data):
        image = data.get("img")
        category = data.get("category")
        group_name = serializer.validated_data.get("name")

        if image:
            self.upload_image(serializer, image)

        if category == "Free":
            serializer.validated_data["price"] = 0
            self.delete_existing_group(group_name)
        elif category == "Paid":
            self.create_or_get_group(group_name)

    @staticmethod
    def upload_image(serializer, image):
        image_name = f"{secrets.token_hex(10)}.{image.name.split('.')[-1]}"
        image_path = os.path.join("media/images", image_name)
        with open(image_path, "wb") as image_file:
            for chunk in image.chunks():
                image_file.write(chunk)
        serializer.validated_data["img"] = image_path.replace("media/", "")

    @staticmethod
    def create_or_get_group(group_name):
        try:
            Group.objects.get(name=group_name)
        except Group.DoesNotExist:
            new_group = Group(name=group_name)
            new_group.save()

    @staticmethod
    def delete_existing_group(group_name):
        try:
            existing_group = Group.objects.get(name=group_name)
            existing_group.delete()
        except Group.DoesNotExist:
            pass


############################### COURSE_VIDEO ###########################################
class CourseVideosView(generics.ListAPIView):
    serializer_class = CourseVideoSerializer

    def get_queryset(self):
        course_id = self.kwargs.get("pk")
        course = get_object_or_404(models.Course, pk=course_id)
        return models.CourseVideo.objects.filter(course=course)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        custom_data = [
            {
                "id": item["id"],
                "name": item["name"],
                "date_uploaded": item["date_uploaded"]
            }
            for item in serializer.data
        ]
        return Response(custom_data)


class CourseVideoQueryView(generics.CreateAPIView):
    serializer_class = CourseVideoSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        course = get_object_or_404(models.Course, pk=kwargs.get("pk"))
        if course.user == request.user:
            serializer = CourseVideoSerializer(data=request.data)
            if serializer.is_valid():
                content = request.data.get("content")
                content_name = f"{secrets.token_hex(5)}.{content.name.split('.')[-1]}"
                content_path = os.path.join("media/videos", content_name)
                with open(content_path, "wb") as content_file:
                    for chunk in content.chunks():
                        content_file.write(chunk)
                serializer.validated_data["content"] = content_path.replace("media/", "")
                serializer.save(course=course)
                return Response(serializer.data)
            return Response({"valid contains": ['MOV', 'avi', 'mp4', 'webm', 'mkv']},
                            status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response("You don't have permission to create this video.", status=status.HTTP_403_FORBIDDEN)


class CourseVideoDetail(generics.RetrieveAPIView):
    serializer_class = CourseVideoSerializer

    def get_object(self):
        course = get_object_or_404(models.Course, pk=self.kwargs.get("pk"))
        video_id = self.kwargs.get("video_id")
        if course.category == "Paid":
            if self.request.user.groups.filter(name=course.name).exists() or \
                    course.user == self.request.user:
                return get_object_or_404(models.CourseVideo, pk=video_id, course=course)
            else:
                return None
        return get_object_or_404(models.CourseVideo, pk=video_id, course=course)


class CourseVideoDeleteView(generics.RetrieveDestroyAPIView):
    serializer_class = CourseVideoSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        course = get_object_or_404(models.Course, pk=kwargs.get("pk"))
        video = get_object_or_404(models.CourseVideo, pk=kwargs.get("video_id"), course=course)
        if course.user == self.request.user:
            video.delete()
            return HttpResponseRedirect(reverse("api:videos", args=(course.pk,)))
        return Response({"status": "You don't have permission to delete the video."}, status=status.HTTP_403_FORBIDDEN)


class CourseVideoUpdateView(generics.RetrieveUpdateAPIView):
    serializer_class = CourseVideoSerializer
    queryset = models.CourseVideo.objects.all()
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return get_object_or_404(models.CourseVideo, pk=self.kwargs.get("video_id"), course__pk=self.kwargs.get("pk"),
                                 course__user=self.request.user)

    def put(self, request, *args, **kwargs):
        course = get_object_or_404(models.Course, pk=kwargs.get("pk"))
        instance_video = self.get_object()
        if course.user == request.user:
            mutable_data = request.data.copy()
            mutable_data['content'] = instance_video.content
            serializer = CourseVideoSerializer(instance_video, data=mutable_data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
        return Response("You don't have permission to update this video.", status=status.HTTP_403_FORBIDDEN)


############################### FAVORITE COURSES ###########################################

class FavoriteCourseView(generics.ListCreateAPIView):
    queryset = FavoriteCourse.objects.all()
    serializer_class = FavoriteCourseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return FavoriteCourse.objects.filter(user=user)

    def perform_create(self, serializer):
        user = self.request.user
        course_id = self.request.data.get('course')
        is_favorite = FavoriteCourse.objects.filter(user=user, course=course_id).exists()

        if is_favorite:
            return None
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        if serializer.instance is None:
            return Response({'message': 'This course is already added to favorites'},
                            status=status.HTTP_400_BAD_REQUEST)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class FavoriteCourseDeleteView(generics.RetrieveDestroyAPIView):
    serializer_class = FavoriteCourseSerializer

    def get_queryset(self):
        user = self.request.user
        return FavoriteCourse.objects.filter(user=user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


############################### COURSE_VIDEO ###########################################


class PayCourseView(generics.ListAPIView):
    serializer_class = BuyCourseSerializerGET
    queryset = models.BuyCourse.objects.all()

    def get_queryset(self):
        return models.BuyCourse.objects.filter(course__user=self.request.user,hidden=False)


class PayCourseQueryView(generics.CreateAPIView):
    serializer_class = BuyCourseSerializerPOST
    queryset = models.BuyCourse.objects.all()
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        course = get_object_or_404(Course, pk=self.kwargs.get("pk"))
        if serializer.is_valid():
            buy_course = serializer.save(user=request.user, course=course)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def access_view(request, purchase_id):
    purchase = get_object_or_404(BuyCourse, pk=purchase_id)

    if request.user == purchase.course.user:
        if purchase.course.category == "Paid":
            try:
                group = Group.objects.get(name=purchase.course.name.strip())
                purchase.user.groups.add(group)
                purchase.user.save()
                if purchase.user.groups.filter(name=purchase.course.name).exists():
                    purchase.hidden = True
                    purchase.save()
                return redirect("api:purchases")
            except Group.DoesNotExist:
                raise Http404("Group not found with such name")

    return redirect("api:courses")


class AddCourseToMyCourse(generics.CreateAPIView):
    queryset = FavoriteCourse.objects.all()
    serializer_class = MyCourseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return MyCourse.objects.filter(user=user)

    def perform_create(self, serializer):
        user = self.request.user
        course_id = self.request.data.get('course')
        course = Course.objects.get(id=course_id)
        is_my_course = MyCourse.objects.filter(user=user, course=course).exists()
        if is_my_course:
            raise Exception("This course is already in My Course")
        if course.category == "Paid":
            bought_course = BuyCourse.objects.filter(user=user, course=course).exists()
            if not bought_course:
                raise Exception("This course is not purchased")
        serializer.save(user=self.request.user, course=course)

    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
        except Exception as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class MyCourseView(generics.ListAPIView):
    serializer_class = MyCourseSerializerGET
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return MyCourse.objects.filter(user=self.request.user)


class CourseImageQueryView(generics.CreateAPIView):
    serializer_class = CourseImageSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        course = get_object_or_404(models.Course, pk=kwargs.get("pk"))
        if course.user == request.user:
            serializer = CourseImageSerializer(data=request.data)
            if serializer.is_valid():
                image = request.data.get("image")
                image_name = f"{secrets.token_hex(5)}.{image.name.split('.')[-1]}"
                image_path = os.path.join("media/", image_name)
                os.makedirs(os.path.dirname("media/"), exist_ok=True)
                with open(image_path, "wb") as image_file:
                    for chunk in image.chunks():
                        image_file.write(chunk)
                serializer.validated_data["image"] = image_path.replace("media/", "")
                serializer.save(course=course)
                return Response(serializer.data)
            return Response({"valid formats": ['JPEG', 'JPG', 'PNG', 'GIF']},
                            status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response("You don't have permission to upload this image.", status=status.HTTP_403_FORBIDDEN)


class CourseImageView(generics.ListAPIView):
    serializer_class = CourseImageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        course_id = self.kwargs.get("pk")
        course = get_object_or_404(Course, pk=course_id)
        if course.category == "Paid":
            if BuyCourse.objects.filter(user=self.request.user, course=course).exists() or \
                    course.user == self.request.user:
                return CourseImage.objects.filter(course=course)
            else:
                return None
        return CourseImage.objects.filter(course=course)

class CourseImageDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CourseImageSerializer
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "image_pk"

    def get_course(self):
        return get_object_or_404(Course, pk=self.kwargs.get("pk"))
    def has_permission_to_access(self, course):
        return self.request.user == course.user
    def get_queryset(self):
        course = self.get_course()
        if not self.has_permission_to_access(course):
            return CourseImage.objects.none()
        return CourseImage.objects.filter(course=course)
    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs.get(self.lookup_url_kwarg))
    def retrieve(self, request, *args, **kwargs):
        if not self.get_queryset().exists():
            return Response({"error": "You do not have permission to access this image"}, status=status.HTTP_403_FORBIDDEN)
        return super().retrieve(request, *args, **kwargs)
    def update(self, request, *args, **kwargs):
        if not self.get_queryset().exists():
            return Response({"error": "You do not have permission to update this image"}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)
    def destroy(self, request, *args, **kwargs):
        if not self.get_queryset().exists():
            return Response({"error": "You do not have permission to delete this image"}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)
